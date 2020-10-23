The Snakemake workflows in this repository can be run so that both the
coordination and running of the jobs is containerised and SLURM-ified. This is
done using the `run_coord.sh` script from the
[singslurm](https://github.com/frankier/singslurm) repository:

    $ SIF_PATH=`pwd`/gsoc2020_skeldump.sif SNAKEMAKE_DIR=/opt/redhen/skeldump/embedtrain/ SLURM_CONF=/opt/redhen/skeldump/contrib/slurm/embedtrain.slurmconf.json ./run_coord.sh

If you want to modify the slurm conf without rebuilding the container you can use SLURM_CONF_ON_HOST:

    $ SIF_PATH=`pwd`/gsoc2020_skeldump.sif SNAKEMAKE_DIR=/opt/redhen/skeldump/embedtrain/ SLURM_CONF=`pwd`/embedtrain.slurmconf.json SLURM_CONF_ON_HOST=1 ./run_coord.sh
