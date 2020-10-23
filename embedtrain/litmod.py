from time import perf_counter
from typing import Any, Dict, List

import torch
from pytorch_lightning.core.lightning import LightningModule
from pytorch_metric_learning import losses, miners, testers
from sklearn.model_selection import StratifiedShuffleSplit
from torch.utils.data import DataLoader, Dataset, Subset, WeightedRandomSampler

from skeldump.skelgraphs.reducer import SkeletonReducer

from . import pt_tb_monkey  # noqa
from .embed_skels import EMBED_SKELS
from .flex_st_gcn import FlexStGcn
from .graph import GraphAdapter
from .pt_datasets import (
    BodySkeletonDataset,
    DataPipeline,
    HandSkeletonDataset,
    SkeletonDataset,
)


class MetGcnLit(LightningModule):
    """
    Class for metric learning using mmskeleton GCNs using PyTorch Lightning.
    """

    dataset: SkeletonDataset
    train_dataset: Dataset
    num_classes: int

    # *Setup

    @staticmethod
    def add_model_specific_args(parent_parser):
        from argparse import ArgumentParser

        parser = ArgumentParser(parents=[parent_parser], add_help=False)
        parser.add_argument(
            "--run-type", type=str, choices=["prod", "eval"], default="eval"
        )
        parser.add_argument("--embed-size", type=int, default=64)
        parser.add_argument("--loss", choices=["nsm", "msl"], default="nsm")
        # Config
        parser.add_argument("--no-aug", action="store_true")
        parser.add_argument("--include-score", action="store_true")
        parser.add_argument("--tester-device")
        return parser

    def __init__(
        self,
        h5fn,
        mode="eval",
        graph="HAND",
        embed_size: int = 64,
        loss="nsm",
        batch_size=32,
        lr=0.001,
        no_aug=False,
        vocab=None,
        include_score=False,
        body_labels=None,
        tester_device=None,
        **kwargs,
    ):
        from argparse import Namespace

        super().__init__()
        if isinstance(h5fn, dict):
            # FIXME: This seems to be the only way to get checkpoint loading
            # working from the hand checkpoint. There must be a better way...
            self.hparams = Namespace(**h5fn)
            assert self.hparams.skel_graph_name == "HAND"
            graph = self.hparams.skel_graph_name
            self.skel_graph = SkeletonReducer(EMBED_SKELS[graph])
            self.num_classes = 58 - 5
        else:
            self.data_path = h5fn
            self.hparams.skel_graph_name = graph
            self.skel_graph = SkeletonReducer(EMBED_SKELS[graph])
            self.tester_device = tester_device
            self.hparams.embed_size = embed_size
            self.hparams.loss_name = loss
            print("MetGcnLit", self.data_path)
            if graph == "HAND":
                self.dataset = HandSkeletonDataset(self.data_path, vocab=vocab)
            else:
                self.dataset = BodySkeletonDataset(
                    self.data_path,
                    vocab=vocab,
                    skel_graph=self.skel_graph,
                    body_labels=body_labels,
                )
            self.hparams.mode = mode
            assert self.hparams.mode in ("eval", "prod")
            self.batch_size = batch_size
            self.hparams.lr = lr
            self.hparams.no_aug = no_aug
            self.hparams.include_score = include_score

            if self.hparams.mode == "prod":
                self.num_classes = self.dataset.num_classes()
            else:
                self.num_classes = (
                    self.dataset.num_classes() - self.dataset.num_left_out()
                )

        self.gcn = FlexStGcn(
            3 if self.hparams.include_score else 2,
            self.hparams.embed_size,
            GraphAdapter(self.skel_graph.dense_skel),
        )

        # Parameters are based on ones that seem reasonable given
        # https://github.com/KevinMusgrave/powerful-benchmarker
        # Could optimise...
        if self.hparams.loss_name == "nsm":
            self.loss = losses.NormalizedSoftmaxLoss(
                num_classes=self.num_classes,
                embedding_size=self.hparams.embed_size,
                temperature=0.07,
            )
        else:
            assert self.hparams.loss_name == "msl"
            self.loss = losses.MultiSimilarityLoss(alpha=10, beta=50, base=0.7)
            self.miner = miners.MultiSimilarityMiner(epsilon=0.5)

        self.val_tester = None
        self.test_tester = None

    def setup(self, stage):
        dataset = self.dataset
        if self.hparams.mode == "prod":
            self.train_dataset = dataset
            sample_weights = dataset.get_sample_weights()
            self.val_dataset = Subset(dataset, [])
            self.test_dataset = Subset(dataset, [])
        else:
            train_val_idxs = []
            train_val_clses = []
            test_idxs = []
            for idx in range(len(dataset)):
                cls = dataset[idx]["category_id"]
                if cls >= self.num_classes:
                    test_idxs.append(idx)
                else:
                    train_val_idxs.append(idx)
                    train_val_clses.append(cls)

            shuffle_splitter = StratifiedShuffleSplit(
                test_size=0.1, train_size=0.9, random_state=0
            )
            train_idxs, val_idxs = next(
                shuffle_splitter.split(train_val_idxs, train_val_clses)
            )
            train_idxs = [train_val_idxs[idx] for idx in train_idxs]
            val_idxs = [train_val_idxs[idx] for idx in val_idxs]

            self.train_dataset = Subset(dataset, train_idxs)
            sample_weights = dataset.get_sample_weights(train_idxs)
            self.val_dataset = Subset(dataset, val_idxs)
            self.test_dataset = Subset(dataset, test_idxs)

        self.train_sampler = WeightedRandomSampler(
            weights=sample_weights,
            num_samples=len(sample_weights) * 20,
            replacement=True,
        )
        self.val_sampler = None
        self.test_sampler = None

        self.train_dataset = self.mk_data_pipeline(self.train_dataset)
        self.val_dataset = self.mk_data_pipeline(self.val_dataset)
        self.test_dataset = self.mk_data_pipeline(self.test_dataset, no_aug=True)

    # *Common

    @staticmethod
    def ges_acc_flat(all_accuracies):
        res = {}
        for seg, accs in all_accuracies.items():
            for name, acc in accs.items():
                res[f"ges_{seg}_{name}"] = acc
        return res

    def batch_loss(self, batch):
        x, y = batch
        y_hat = self(x)
        if self.hparams.loss_name == "nsm":
            return self.loss(y_hat, y)
        else:
            assert self.hparams.loss_name == "msl"
            hard_pairs = self.miner(y_hat, y)
            return self.loss(y_hat, y, hard_pairs)

    def mk_data_pipeline(self, dataset, no_aug=False):
        from mmskeleton.datasets.skeleton import (
            mask_by_visibility,
            normalize_by_resolution,
            simulate_camera_moving,
            to_tuple,
            transpose,
        )

        aug_steps: List[Dict[str, Any]] = []
        if not self.hparams.no_aug or no_aug:
            aug_steps.extend((dict(stage=simulate_camera_moving),))
        if not self.hparams.include_score:
            aug_steps.append(
                dict(stage=lambda data: {**data, "data": data["data"][:2, :, :, :]})
            )
        return DataPipeline(
            dataset,
            [
                dict(stage=normalize_by_resolution),
                dict(stage=mask_by_visibility),
                *aug_steps,
                dict(stage=transpose, order=[0, 2, 1, 3]),
                dict(stage=to_tuple),
            ],
        )

    def mk_data_loader(self, dataset, **kwargs):
        return DataLoader(
            dataset,
            batch_size=self.batch_size,
            num_workers=4,
            pin_memory=True,
            **kwargs,
        )

    # *Training

    def forward(self, x):
        return self.gcn(x)

    def training_step(self, batch, batch_idx):
        loss = self.batch_loss(batch)
        return {"loss": loss, "log": {"train_loss": loss}}

    def configure_optimizers(self):
        return torch.optim.SGD(
            self.parameters(),
            lr=self.hparams.lr,
            momentum=0.9,
            nesterov=True,
            weight_decay=0.0001,
        )
        # return torch.optim.Adam(self.parameters(), lr=self.lr)

    def train_dataloader(self):
        return self.mk_data_loader(self.train_dataset, sampler=self.train_sampler)

    # *Validation

    def validation_step(self, batch, batch_idx):
        loss = self.batch_loss(batch)
        return {"val_loss": loss, "log": {"val_loss": loss}}

    def mk_tester(self, *args, **kwargs):
        return testers.GlobalEmbeddingSpaceTester(
            *args,
            dataloader_num_workers=4,
            data_device=self.tester_device
            if self.tester_device is not None
            else self.device,
            end_of_testing_hook=self.end_of_testing_hook,
            **kwargs,
        )

    def get_val_tester(self) -> testers.GlobalEmbeddingSpaceTester:
        if self.val_tester is None:
            self.val_tester = self.mk_tester()
        return self.val_tester

    def get_test_tester(self) -> testers.GlobalEmbeddingSpaceTester:
        if self.test_tester is None:
            self.test_tester = self.mk_tester("compared_to_sets_combined")
        return self.test_tester

    def end_of_testing_hook(self, tester):
        # Add embedding for projector / reprojection
        for split_name, (embeddings, labels) in tester.embeddings_and_labels.items():
            self.logger.experiment.add_embedding(
                embeddings,
                labels,
                global_step=self.global_step,
                tag=f"proj_{split_name}",
            )

    def validation_epoch_end(self, outputs):
        if self.hparams.mode == "prod":
            return
        val_loss_mean = torch.stack([x["val_loss"] for x in outputs]).mean()
        tester = self.get_val_tester()
        start = perf_counter()
        tester.test(
            {"train": self.train_dataset, "val": self.val_dataset,},
            self.current_epoch,
            self,
            splits_to_eval=["val"],
        )
        print("Validation tester took {}s".format(perf_counter() - start))
        return {
            "val_loss": val_loss_mean,
            "val_map": tester.all_accuracies["val"][
                "mean_average_precision_at_r_level0"
            ],
            "log": {
                "val_loss": val_loss_mean,
                **self.ges_acc_flat(tester.all_accuracies),
            },
        }

    def val_dataloader(self):
        return self.mk_data_loader(self.val_dataset, sampler=self.val_sampler)

    # *Testing

    def test_step(self, batch, batch_idx):
        # Everything is done by GlobalEmbeddingSpaceTester
        return {}

    def test_epoch_end(self, outputs):
        if self.hparams.mode == "prod":
            return
        tester = self.get_test_tester()
        tester.test(
            {
                "train": self.train_dataset,
                "val": self.val_dataset,
                "test": self.test_dataset,
            },
            self.current_epoch,
            self,
            splits_to_eval=["test"],
        )
        return {"log": self.ges_acc_flat(tester.all_accuracies)}

    def test_dataloader(self):
        return self.mk_data_loader(self.test_dataset, sampler=self.test_sampler)
