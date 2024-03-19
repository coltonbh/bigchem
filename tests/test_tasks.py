import json

import numpy as np
import pytest
from qcio import (
    CalcType,
    OptimizationOutput,
    ProgramFailure,
    ProgramInput,
    SinglePointOutput,
    SubProgramArgs,
)
from qcop.exceptions import QCOPBaseError

from bigchem.canvas import group  # type:ignore
from bigchem.tasks import assemble_hessian, compute, frequency_analysis, output_to_input


def test_hessian_task(test_data_dir, water):
    """Ensure that my hessian implementation matches previous result from Umberto"""

    with open(test_data_dir / "hessian_gradients.json") as f:
        gradients = [SinglePointOutput(**g) for g in json.load(f)]

    # Testing task in foreground since no QC package is required
    # 5.03e-3 was the dh used to create these gradients
    result = assemble_hessian(gradients, 5.0e-3)

    answer = SinglePointOutput.model_validate_json(
        (test_data_dir / "hessian_answer.json").read_text()
    )

    np.testing.assert_almost_equal(
        result.return_result, answer.return_result, decimal=7
    )
    assert result.input_data.calctype == "hessian"


def compare_eigenvector_arrays(arr1, arr2, decimal=6):
    for i, (vec1, vec2) in enumerate(
        zip(arr1.reshape(-1, arr1.shape[-1]), arr2.reshape(-1, arr2.shape[-1]))
    ):
        try:
            np.testing.assert_almost_equal(vec1, vec2, decimal=decimal)
        except AssertionError:
            np.testing.assert_almost_equal(
                vec1,
                -vec2,
                decimal=decimal,
                err_msg=f"Eigenvectors at position {i} are not equal even considering "
                "a sign difference.",
            )


def test_frequency_analysis_task(test_data_dir):
    hessian_ar = SinglePointOutput.model_validate_json(
        (test_data_dir / "hessian_answer.json").read_text()
    )
    output = frequency_analysis(hessian_ar)

    answer = SinglePointOutput.model_validate_json(
        (test_data_dir / "frequency_analysis_answer.json").read_text()
    )

    np.testing.assert_almost_equal(
        output.results.freqs_wavenumber,
        answer.results.freqs_wavenumber,
        decimal=0,
    )
    compare_eigenvector_arrays(
        output.results.normal_modes_cartesian,
        answer.results.normal_modes_cartesian,
        decimal=4,
    )
    np.testing.assert_almost_equal(
        output.results.gibbs_free_energy,
        answer.results.gibbs_free_energy,
        decimal=2,
    )


def test_frequency_analysis_task_kwargs(test_data_dir):
    hessian_ar = SinglePointOutput.model_validate_json(
        (test_data_dir / "hessian_answer.json").read_text()
    )
    answer = SinglePointOutput.model_validate_json(
        (test_data_dir / "frequency_analysis_answer.json").read_text()
    )

    output = frequency_analysis(hessian_ar, temperature=310, pressure=1.2)

    np.testing.assert_almost_equal(
        output.results.freqs_wavenumber,
        answer.results.freqs_wavenumber,
        decimal=0,
    )
    compare_eigenvector_arrays(
        output.results.normal_modes_cartesian,
        answer.results.normal_modes_cartesian,
        decimal=4,
    )

    np.testing.assert_almost_equal(
        output.results.gibbs_free_energy,
        -76.38277740247364,  # Different number from answer computed with no kwargs
        decimal=2,
    )


@pytest.mark.parametrize(
    "program,model,keywords,batch",
    (
        ("psi4", {"method": "HF", "basis": "sto-3g"}, {}, False),
        ("rdkit", {"method": "UFF"}, {}, False),
        ("xtb", {"method": "GFN2-xTB"}, {"accuracy": 1.0, "max_iterations": 20}, False),
        ("xtb", {"method": "GFN2-xTB"}, {"accuracy": 1.0, "max_iterations": 20}, True),
    ),
)
@pytest.mark.timeout(65)
def test_compute(hydrogen, program, model, keywords, batch):
    """Testings as one function so we don't submit excess compute jobs.

    NOTE: Timeout is long because the worker instance may be waiting to connect to
    RabbitMQ if it just started up. Celery's exponential back off means that
    it's possible a few early misses on worker -> MQ connection results in the
    worker waiting up for 8 seconds (or longer) to retry connecting.
    """
    prog_input = ProgramInput(
        molecule=hydrogen, calctype="energy", model=model, keywords=keywords
    )
    sig = compute.s(program, prog_input)
    if batch:
        # Make list of inputs
        sig = group([sig] * 2)

    # Submit Job
    future_result = sig.delay()
    result = future_result.get()

    # Assertions
    assert future_result.ready() is True

    # Check assertions about single results and groups
    if not isinstance(result, list):
        result = [result]
    for r in result:
        assert isinstance(r, SinglePointOutput)


def test_result_to_input_optimization_result(water, sp_output):
    opt_result = OptimizationOutput(
        input_data={
            "model": {"method": "b3lyp", "basis": "6-31g"},
            "molecule": water,
            "calctype": CalcType.optimization,
        },
        provenance={"program": "fake-program"},
        results={"trajectory": [sp_output]},
    )

    program_args = SubProgramArgs(
        **{
            "keywords": {"program": "new_prog"},
            "subprogram_args": {
                "model": {"method": "new_methods", "basis": "new_basis"}
            },
            "subprogram": "new_subprogram",
            "extras": {"ex1": "ex1"},
        }
    )
    new_input = output_to_input(opt_result, CalcType.optimization, program_args)
    assert new_input.subprogram_args.model == program_args.subprogram_args.model

    assert new_input.keywords == program_args.keywords
    assert new_input.extras == program_args.extras


def test_program_failure_serialized_when_raised_in_worker(hydrogen):
    # Basis misspelled to trigger failure
    prog_input = ProgramInput(
        molecule=hydrogen, calctype="energy", model={"method": "b3lyp", "basis": "fake"}
    )

    # Submit Job
    future_result = compute.delay("psi4", prog_input)
    try:
        future_result.get()
    except QCOPBaseError as e:
        assert isinstance(e, QCOPBaseError)
        assert isinstance(e.program_failure, ProgramFailure)
