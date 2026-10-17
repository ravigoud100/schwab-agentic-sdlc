"""Schema-valid requirement fixtures for tests of downstream workflow stages."""
import json
from llm.provider import ModelResponse


def requirements_payload(problem='Implement the requested behavior', ambiguities=None):
    return {'normalized_problem':problem,'functional_requirements':[problem],
            'non_functional_requirements':['Preserve existing behavior'],
            'acceptance_criteria':['Required validation passes'],
            'ambiguities':ambiguities or [],'risks':[],'classification':'brownfield'}


def requirements_response(problem='Implement the requested behavior', ambiguities=None):
    return ModelResponse(json.dumps(requirements_payload(problem,ambiguities)))
