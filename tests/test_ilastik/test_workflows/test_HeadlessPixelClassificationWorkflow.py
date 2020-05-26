from pathlib import Path
import shutil
import subprocess
import logging
import tempfile
import json

import pytest
import numpy
import vigra
import h5py
import z5py
import zipfile
from ndstructs import Array5D, Shape5D

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

try:
    from lazyflow.distributed.TaskOrchestrator import TaskOrchestrator

    MPI_DEPENDENCIES_MET = bool(shutil.which("mpirun"))
except ImportError:
    MPI_DEPENDENCIES_MET = False


@pytest.fixture
def sample_projects_dir(tmp_path: Path) -> Path:
    test_data_path = Path(__file__).parent.parent / "data"
    sample_projects_zip_path = test_data_path / "test_projects.zip"
    sample_data_dir_path = test_data_path / "inputdata"

    projects_archive = zipfile.ZipFile(sample_projects_zip_path, mode="r")
    projects_archive.extractall(path=tmp_path)

    shutil.copytree(sample_data_dir_path, tmp_path / "inputdata")

    return tmp_path


@pytest.fixture
def pixel_classification_ilp_2d3c(sample_projects_dir: Path) -> Path:
    return sample_projects_dir / "PixelClassification2d3c.ilp"


def create_h5(data: numpy.ndarray, axiskeys: str) -> Path:
    assert len(axiskeys) == len(data.shape)
    path = tempfile.mkstemp()[1] + ".h5"
    with h5py.File(path, "w") as f:
        ds = f.create_dataset("data", data=data)
        ds.attrs["axistags"] = vigra.defaultAxistags(axiskeys).toJSON()

    return Path(path) / "data"


class FailedHeadlessExecutionException(Exception):
    pass


def run_headless_pixel_classification(
    *,
    num_distributed_workers: int = 0,
    project: Path,
    raw_data: Path,
    output_filename_format: str,
    input_axes: str = "",
    output_format: str = "hdf5",
    ignore_training_axistags: bool = False,
):
    assert project.exists()
    assert raw_data.parent.exists()

    subprocess.run(["which", "python"])
    ilastik_dot_py = Path(__file__).parent.parent.parent.parent / "ilastik.py"
    subprocess_args = [
        "python",
        str(ilastik_dot_py),
        "--headless",
        "--project=" + str(project),
        "--raw-data=" + str(raw_data),
        "--output_filename_format=" + str(output_filename_format),
        "--output_format=" + output_format,
    ]

    if input_axes:
        subprocess_args.append("--input-axes=" + input_axes)

    if ignore_training_axistags:
        subprocess_args.append("--ignore_training_axistags")

    if num_distributed_workers:
        subprocess_args = ["mpirun", "-N", str(num_distributed_workers)] + subprocess_args + ["--distributed"]

    result = subprocess.run(subprocess_args, capture_output=True)  # switch to False if debugging stuck processes
    if result.returncode != 0:
        raise FailedHeadlessExecutionException(
            "===STDOUT===\n\n" + result.stdout.decode("utf8") + "\n\n===STDERR===\n\n" + result.stderr.decode("utf8")
        )


def test_headless_2d3c_with_same_raw_data_axis(pixel_classification_ilp_2d3c: Path, tmp_path: Path):
    raw_100x100y3c: Path = create_h5(numpy.random.rand(100, 100, 3), axiskeys="yxc")
    output_path = tmp_path / "out_100x100y3c.h5"
    run_headless_pixel_classification(
        project=pixel_classification_ilp_2d3c, raw_data=raw_100x100y3c, output_filename_format=str(output_path)
    )


def test_headless_2d3c_with_swizzled_raw_data_axis(pixel_classification_ilp_2d3c: Path, tmp_path: Path):
    raw_3c100x100y: Path = create_h5(numpy.random.rand(3, 100, 100), axiskeys="cyx")
    output_path = tmp_path / "out_3c100x100y.h5"

    # default behavior is to try to apply training axistags to the batch data, and therefore fail because raw data's
    # dimensions do not match that of the training data
    with pytest.raises(FailedHeadlessExecutionException):
        run_headless_pixel_classification(
            project=pixel_classification_ilp_2d3c, raw_data=raw_3c100x100y, output_filename_format=str(output_path)
        )

    # forcing correct input axes should pass
    run_headless_pixel_classification(
        project=pixel_classification_ilp_2d3c,
        raw_data=raw_3c100x100y,
        output_filename_format=str(output_path),
        input_axes="cyx",
    )

    # alternatively, since the generated h5 data has the axistags property, we can ignore training data and use that
    # instead, by using the '--ignore_training_axistags' flag
    run_headless_pixel_classification(
        project=pixel_classification_ilp_2d3c,
        raw_data=raw_3c100x100y,
        output_filename_format=str(output_path),
        ignore_training_axistags=True,
    )


@pytest.mark.skipif(not MPI_DEPENDENCIES_MET, reason="Must have mpi4py and mpirun installed fot this test")
def test_distributed_results_are_identical_to_single_process_results(
    pixel_classification_ilp_2d3c: Path, tmp_path: Path
):
    raw_100x100y3c: Path = create_h5(numpy.random.rand(100, 100, 3), axiskeys="yxc")

    single_process_output_path = tmp_path / "single_process_out_100x100y3c.h5"
    run_headless_pixel_classification(
        project=pixel_classification_ilp_2d3c,
        raw_data=raw_100x100y3c,
        output_filename_format=str(single_process_output_path),
    )

    with h5py.File(single_process_output_path, "r") as f:
        single_process_out_data = f["exported_data"][()]

    distributed_output_path = tmp_path / "distributed_out_100x100y3c.n5"
    run_headless_pixel_classification(
        num_distributed_workers=4,
        output_format="n5",
        project=pixel_classification_ilp_2d3c,
        raw_data=raw_100x100y3c,
        output_filename_format=str(distributed_output_path),
    )

    with z5py.File(distributed_output_path, "r") as f:
        distributed_out_data = f["exported_data"][()]

    assert (single_process_out_data == distributed_out_data).all()
