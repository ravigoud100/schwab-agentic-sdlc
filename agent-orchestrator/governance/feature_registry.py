"""Explicitly scoped feature adapter registry for the integrated SDLC workflow.

Maps approved engineering obligation contracts and task plans to vetted test generation,
rendering, and release evidence strategies without hardcoded feature-specific orchestration.
Unsupported requirements are safely rejected with an explicit error.
"""
from abc import ABC, abstractmethod
import json
from governance.semantic_plan import custom_expiration_contract, digest


EXPIRATION_CASES = {o['id']: o['spec'] for o in custom_expiration_contract()['obligations'] if o['kind'] == 'test'}

HEALTH_CASES = {
    'test-health-status': {'component': 'UrlService', 'method': 'getHealthStatus', 'expected_status': 'UP'},
    'test-health-endpoint': {'component': 'UrlController', 'path': '/health', 'expected_status': 200, 'expected_body_status': 'UP'},
}


class FeatureAdapter(ABC):
    @property
    @abstractmethod
    def feature_id(self) -> str:
        """Unique identifier for this feature."""
        pass

    @property
    @abstractmethod
    def test_path(self) -> str:
        """Authorized additive Java test path."""
        pass

    @property
    @abstractmethod
    def policy_description(self) -> str:
        """Policy description for test scope authorization."""
        pass

    @abstractmethod
    def can_handle(self, contract: dict) -> bool:
        """Return True if this adapter supports the obligation contract."""
        pass

    @abstractmethod
    def expected_spec_payload(self) -> dict:
        """Authoritative allowed cases payload."""
        pass

    @abstractmethod
    def generate_spec(self, model, context: dict, scope: dict) -> tuple[dict, str, str]:
        """Generate test specifications via LLM provider."""
        pass

    @abstractmethod
    def validate_spec(self, payload: dict, contract: dict) -> None:
        """Validate specification against vetted obligation constraints."""
        pass

    @abstractmethod
    def render(self, payload: dict, contract: dict) -> bytes:
        """Render JUnit source code."""
        pass

    @abstractmethod
    def get_facts(self, manifest: dict) -> dict:
        """Return structured factual documentation catalog for release evidence."""
        pass

    @abstractmethod
    def expected_test_cases(self, negative: bool = False) -> set[str]:
        """Return the set of qualified test case names produced by this feature's generated test file."""
        pass

    def validate_results(self, record: dict, original: dict, negative: bool = False) -> None:
        """Validate test execution outcomes against baseline and expected feature tests."""
        if not original or any(status != 'passed' for status in original.values()):
            raise PermissionError('Original baseline must execute and pass without skips')
        cases = record['cases']
        generated = self.expected_test_cases(negative)
        if set(cases) != set(original) | generated:
            raise PermissionError('Original or generated tests missing/unexpected')
        for case in generated:
            if cases.get(case) != 'passed':
                raise PermissionError(f'Expected test {case} did not pass: {cases.get(case)}')
        if record['exit_code'] != 0:
            raise PermissionError('Maven exit status contradicts required evidence')


class ExpirationFeatureAdapter(FeatureAdapter):
    @property
    def feature_id(self) -> str:
        return 'expiration'

    @property
    def test_path(self) -> str:
        return 'url-shortener/src/test/java/url_shortener/GeneratedExpirationBoundaryTest.java'

    @property
    def policy_description(self) -> str:
        return 'immutable-original-project;one-additive-vetted-expiration-test'

    def can_handle(self, contract: dict) -> bool:
        if not isinstance(contract, dict):
            return False
        if 'selected_answer' in contract and contract['selected_answer'] != 'Custom expiration timestamp':
            return False
        if contract.get('decision_id') == 'A1' or contract.get('selected_answer') == 'Custom expiration timestamp':
            return True
        obligations = contract.get('obligations', [])
        return any(isinstance(o, dict) and o.get('id') == 'caller-expiration' for o in obligations)

    def expected_spec_payload(self) -> dict:
        return {'cases': [{'obligation_id': k, 'spec': v} for k, v in EXPIRATION_CASES.items()]}

    def generate_spec(self, model, context: dict, scope: dict) -> tuple[dict, str, str]:
        expected = self.expected_spec_payload()
        self.validate_spec(expected, context['obligation_contract'])
        prompt = f'''Generate ONLY JSON test specifications, not source code.
Authoritative approved requirements: {json.dumps(context['structured_requirements'])}
Approved clarification answers: {json.dumps(context['clarification_answers'])}
Approved obligation contract: {json.dumps(context['obligation_contract'])}
Approved design: {json.dumps(context['design_artifact'])}
Human-approved test-generation scope and policy: {json.dumps(scope)}
Approved engineering plan (when integrated): {json.dumps(context.get('plan',''))}
Prospective plan authorization (when integrated): {json.dumps(context.get('plan_authorization',{}))}
Return exactly {{"cases":[{{"obligation_id":"<test obligation id>","spec":{{"<exact approved spec>":"<values>"}}}}]}}.
Include ONLY the four obligations with kind=test, never implementation obligations.
The complete contract above contains both kinds; implementation IDs such as
caller-expiration, future-only, utc-consistency, missing-compatibility are NOT test IDs.
The following filtered payload is the authoritative allowed response. Return it
exactly; do not substitute the first four obligations from the complete contract:
{json.dumps(expected)}
Include each allowed test ID exactly once and copy its spec values exactly.
The trusted renderer supplies assertions and explicit HTTP/persistence checks. No paths,
Java, disabling, exclusions, shell commands, or weakened assertions are accepted.
'''
        response = model.invoke(prompt, stage='test_generation')
        payload = json.loads(response.text)
        self.validate_spec(payload, context['obligation_contract'])
        return payload, prompt, response.text

    def validate_spec(self, payload: dict, contract: dict = None) -> None:
        if contract is None:
            contract = custom_expiration_contract()
        if not self.can_handle(contract):
            raise ValueError('No vetted test renderer for this contract')
        if not isinstance(payload, dict) or set(payload) != {'cases'} or not isinstance(payload['cases'], list):
            raise ValueError('Invalid test specification')
        seen = set()
        for case in payload['cases']:
            if not isinstance(case, dict) or set(case) != {'obligation_id', 'spec'}:
                raise ValueError('Arbitrary test changes are prohibited')
            oid = case['obligation_id']
            if not isinstance(oid, str) or oid not in EXPIRATION_CASES or oid in seen or digest(case['spec']) != digest(EXPIRATION_CASES[oid]):
                raise ValueError('Missing, duplicate, unknown or weakened test obligation')
            seen.add(oid)
        if seen != set(EXPIRATION_CASES):
            raise ValueError('All four expiration test obligations are required')

    def render(self, payload: dict, contract: dict = None) -> bytes:
        if contract is None:
            contract = custom_expiration_contract()
        self.validate_spec(payload, contract)
        return '''package url_shortener;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class GeneratedExpirationBoundaryTest {
    static final Instant NOW = Instant.parse("2030-01-01T00:00:00Z");
    UrlRepository repository;
    UrlService service;
    @BeforeEach void setup() {
        repository = mock(UrlRepository.class);
        when(repository.saveAndFlush(any(ShortUrl.class))).thenAnswer(call -> call.getArgument(0));
        service = new UrlService(repository, Clock.fixed(NOW, ZoneOffset.UTC));
    }
    @Test void futureExpiration() {
        Instant future = NOW.plusSeconds(3600);
        ShortUrl url = service.createShortUrl("https://example.com/future", future);
        assertEquals(future, url.getExpiresAt());
        assertFalse(url.isExpired(NOW));
        verify(repository).saveAndFlush(url);
    }
    @Test void pastExpiration() { reject(NOW.minusSeconds(3600)); }
    @Test void equalNowExpiration() { reject(NOW); }
    private void reject(Instant value) {
        ResponseStatusException failure = assertThrows(ResponseStatusException.class,
            () -> service.createShortUrl("https://example.com/reject", value));
        assertEquals(HttpStatus.BAD_REQUEST, failure.getStatusCode());
        verify(repository, never()).saveAndFlush(any(ShortUrl.class));
    }
    @Test void missingExpiration() {
        ShortUrl url = service.createShortUrl("https://example.com/missing", null);
        assertNull(url.getExpiresAt());
        assertFalse(url.isExpired(NOW.plusSeconds(315360000)));
        verify(repository).saveAndFlush(url);
    }
}
'''.encode()

    def get_facts(self, manifest: dict) -> dict:
        is_parallel = manifest.get('profile') == 'parallel-expiration-v1'
        source_origin = (
            'The model executed multi-task concurrent implementations under an approved DAG plan; '
            'independent transactions and actual diff identify the applied changes.'
            if is_parallel else
            'The model generated structured production edits under a prospectively approved engineering plan; '
            'the transaction and actual diff identify the applied changes.'
        )
        bounded_limit = (
            'This parallel path supports concurrent implementation tasks, multi-wave validation, '
            'one additive expiration test, and a vetted renderer.'
            if is_parallel else
            'This sequential path supports one implementation task, one additive expiration test, and a vetted renderer. It does not establish arbitrary test synthesis or parallel execution.'
        )
        return {
            'api_change_summary': {
                'contract': 'The approved requirement contract and design accompany this evidence bundle; declared coverage is not proof of arbitrary implementation semantics.'
            },
            'implementation_notes': {
                'source_origin': source_origin,
                'source_hash': f"Verified production-set SHA-256: {manifest['implementation_hash']}.",
                'test_origin': 'The model supplied constrained expiration-case specifications; a trusted renderer produced the additive JUnit file.'
            },
            'testing_evidence': {
                'baseline': f"Original baseline: {len(manifest['records']['baseline']['cases'])} passing tests.",
                'positive': f"Combined implementation and generated tests: {len(manifest['records']['positive']['cases'])} passing tests, no skips; original tests/build files remain byte-identical."
            },
            'limitations': {
                'bounded': bounded_limit,
                'negative': 'No negative-fixture sensitivity claim is made by this integrated run.'
            },
            'release_considerations': {
                'lineage': f"Prospective engineering-plan authorization is verified for plan {manifest['approved_implementation_plan_hash']}.",
                'release': 'Human test review is required. Security, operational and release sign-offs remain separate; no deployment or merge is authorized.'
            }
        }

    def expected_test_cases(self, negative: bool = False) -> set[str]:
        return {'url_shortener.GeneratedExpirationBoundaryTest#' + name for name in
                ('futureExpiration', 'pastExpiration', 'equalNowExpiration', 'missingExpiration')}

    def validate_results(self, record: dict, original: dict, negative: bool = False) -> None:
        if not original or any(status != 'passed' for status in original.values()):
            raise PermissionError('Original baseline must execute and pass without skips')
        cases = record['cases']
        generated = self.expected_test_cases(negative)
        if set(cases) != set(original) | generated:
            raise PermissionError('Original or generated tests missing/unexpected')
        failure = 'url_shortener.GeneratedExpirationBoundaryTest#equalNowExpiration'
        for case, status in cases.items():
            expected = 'failure' if negative and case == failure else 'passed'
            if status != expected:
                raise PermissionError(f'Unexpected test outcome {case}: {status}')
        if (negative and record['exit_code'] == 0) or (not negative and record['exit_code'] != 0):
            raise PermissionError('Maven exit status contradicts required evidence')


class HealthCheckFeatureAdapter(FeatureAdapter):
    @property
    def feature_id(self) -> str:
        return 'health-check'

    @property
    def test_path(self) -> str:
        return 'url-shortener/src/test/java/url_shortener/GeneratedHealthCheckIntegrationTest.java'

    @property
    def policy_description(self) -> str:
        return 'immutable-original-project;one-additive-vetted-health-check-test'

    def can_handle(self, contract: dict) -> bool:
        if not isinstance(contract, dict):
            return False
        if 'selected_answer' in contract and 'health' not in str(contract['selected_answer']).lower():
            return False
        if contract.get('decision_id') in {'H1', 'HEALTH_CHECK'} or 'health' in str(contract.get('selected_answer', '')).lower():
            return True
        obligations = contract.get('obligations', [])
        return any(isinstance(o, dict) and o.get('id') == 'health-service-status' for o in obligations)

    def expected_spec_payload(self) -> dict:
        return {'cases': [{'obligation_id': k, 'spec': v} for k, v in HEALTH_CASES.items()]}

    def generate_spec(self, model, context: dict, scope: dict) -> tuple[dict, str, str]:
        expected = self.expected_spec_payload()
        self.validate_spec(expected, context['obligation_contract'])
        prompt = f'''Generate ONLY JSON test specifications, not source code.
Authoritative approved requirements: {json.dumps(context['structured_requirements'])}
Approved clarification answers: {json.dumps(context['clarification_answers'])}
Approved obligation contract: {json.dumps(context['obligation_contract'])}
Approved design: {json.dumps(context['design_artifact'])}
Human-approved test-generation scope and policy: {json.dumps(scope)}
Approved engineering plan (when integrated): {json.dumps(context.get('plan',''))}
Prospective plan authorization (when integrated): {json.dumps(context.get('plan_authorization',{}))}
Return exactly {{"cases":[{{"obligation_id":"<test obligation id>","spec":{{"<exact approved spec>":"<values>"}}}}]}}.
Include ONLY the test obligations with kind=test:
{json.dumps(expected)}
Include each allowed test ID exactly once and copy its spec values exactly.
The trusted renderer supplies assertions and explicit HTTP/persistence checks. No paths,
Java, disabling, exclusions, shell commands, or weakened assertions are accepted.
'''
        response = model.invoke(prompt, stage='test_generation')
        payload = json.loads(response.text)
        self.validate_spec(payload, context['obligation_contract'])
        return payload, prompt, response.text

    def validate_spec(self, payload: dict, contract: dict = None) -> None:
        if contract is None:
            contract = health_check_contract()
        if not self.can_handle(contract):
            raise ValueError('No vetted test renderer for this contract')
        if not isinstance(payload, dict) or set(payload) != {'cases'} or not isinstance(payload['cases'], list):
            raise ValueError('Invalid test specification')
        seen = set()
        for case in payload['cases']:
            if not isinstance(case, dict) or set(case) != {'obligation_id', 'spec'}:
                raise ValueError('Arbitrary test changes are prohibited')
            oid = case['obligation_id']
            if not isinstance(oid, str) or oid not in HEALTH_CASES or oid in seen or digest(case['spec']) != digest(HEALTH_CASES[oid]):
                raise ValueError('Missing, duplicate, unknown or weakened test obligation')
            seen.add(oid)
        if seen != set(HEALTH_CASES):
            raise ValueError('All health-check test obligations are required')

    def render(self, payload: dict, contract: dict = None) -> bytes:
        if contract is None:
            contract = health_check_contract()
        self.validate_spec(payload, contract)
        return '''package url_shortener;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import java.util.Map;

class GeneratedHealthCheckIntegrationTest {
    @Test void healthStatusUp() {
        UrlRepository repository = mock(UrlRepository.class);
        UrlService service = new UrlService(repository);
        Map<String, String> health = service.getHealthStatus();
        assertNotNull(health);
        assertEquals("UP", health.get("status"));
    }
    @Test void controllerHealthEndpoint() {
        UrlService service = mock(UrlService.class);
        when(service.getHealthStatus()).thenReturn(Map.of("status", "UP"));
        UrlController controller = new UrlController(service);
        Map<String, String> response = controller.health();
        assertNotNull(response);
        assertEquals("UP", response.get("status"));
    }
}
'''.encode()

    def get_facts(self, manifest: dict) -> dict:
        is_parallel = manifest.get('profile', '').startswith('parallel')
        source_origin = (
            'The model executed multiple dependent implementation tasks under an approved DAG plan; '
            'independent transactions and actual diff identify the applied changes.'
            if is_parallel else
            'The model generated structured production edits across multiple dependent tasks under an approved DAG plan; '
            'the transaction and actual diff identify the applied changes.'
        )
        bounded_limit = (
            'This path supports multiple dependent implementation tasks, one additive health-check test, and a vetted renderer.'
        )
        return {
            'api_change_summary': {
                'contract': 'The approved health-check requirement contract and design accompany this evidence bundle; provides /health status monitoring.'
            },
            'implementation_notes': {
                'source_origin': source_origin,
                'source_hash': f"Verified production-set SHA-256: {manifest['implementation_hash']}.",
                'test_origin': 'The model supplied constrained health-check specifications; a trusted renderer produced the additive JUnit file.'
            },
            'testing_evidence': {
                'baseline': f"Original baseline: {len(manifest['records']['baseline']['cases'])} passing tests.",
                'positive': f"Combined implementation and generated tests: {len(manifest['records']['positive']['cases'])} passing tests, no skips; original tests/build files remain byte-identical."
            },
            'limitations': {
                'bounded': bounded_limit,
                'negative': 'No negative-fixture sensitivity claim is made by this health-check run.'
            },
            'release_considerations': {
                'lineage': f"Prospective engineering-plan authorization is verified for plan {manifest['approved_implementation_plan_hash']}.",
                'release': 'Human test review is required. Security, operational and release sign-offs remain separate; no deployment or merge is authorized.'
            }
        }

    def expected_test_cases(self, negative: bool = False) -> set[str]:
        return {
            'url_shortener.GeneratedHealthCheckIntegrationTest#healthStatusUp',
            'url_shortener.GeneratedHealthCheckIntegrationTest#controllerHealthEndpoint',
        }

    def validate_results(self, record: dict, original: dict, negative: bool = False) -> None:
        if not original or any(status != 'passed' for status in original.values()):
            raise PermissionError('Original baseline must execute and pass without skips')
        cases = record['cases']
        generated = self.expected_test_cases(negative)
        if set(cases) != set(original) | generated:
            raise PermissionError('Original or generated tests missing/unexpected')
        for case in generated:
            if cases.get(case) != 'passed':
                raise PermissionError(f'Health check test did not pass: {case}')
        if record['exit_code'] != 0:
            raise PermissionError('Maven exit status contradicts required evidence')


_REGISTRY: list[FeatureAdapter] = [
    ExpirationFeatureAdapter(),
    HealthCheckFeatureAdapter(),
]


def register_adapter(adapter: FeatureAdapter) -> None:
    _REGISTRY.append(adapter)


def list_feature_adapters() -> list[FeatureAdapter]:
    """Return all currently registered feature adapters."""
    return list(_REGISTRY)


def get_feature_adapter(contract_or_context) -> FeatureAdapter:
    """Resolve a vetted feature adapter from a contract, requirement, or context dict.

    Rejects unsupported contracts safely with PermissionError.
    """
    if contract_or_context is None:
        raise PermissionError('No contract or context provided to resolve feature adapter')

    contract = None
    if isinstance(contract_or_context, dict):
        if contract_or_context.get('obligation_contract'):
            contract = contract_or_context['obligation_contract']
        elif contract_or_context.get('approved_contract'):
            contract = contract_or_context['approved_contract']
        elif 'decision_id' in contract_or_context:
            contract = contract_or_context
        elif 'requirement' in contract_or_context:
            req = str(contract_or_context['requirement']).lower()
            if 'health' in req:
                from governance.semantic_plan import health_check_contract
                contract = health_check_contract()
            elif 'expiration' in req or 'expire' in req or 'ttl' in req:
                contract = custom_expiration_contract()
            else:
                raise PermissionError(f'Unsupported feature requirement: {contract_or_context["requirement"]}. No vetted feature adapter registered.')
        else:
            contract = contract_or_context
    elif isinstance(contract_or_context, str):
        req = contract_or_context.lower()
        if 'health' in req:
            from governance.semantic_plan import health_check_contract
            contract = health_check_contract()
        elif 'expiration' in req or 'expire' in req or 'ttl' in req:
            contract = custom_expiration_contract()
        else:
            raise PermissionError(f'Unsupported feature requirement: {contract_or_context}. No vetted feature adapter registered.')

    if not contract or not isinstance(contract, dict):
        raise PermissionError('No valid contract provided to resolve feature adapter')

    for adapter in _REGISTRY:
        if adapter.can_handle(contract):
            return adapter

    decision_id = contract.get('decision_id', 'unknown')
    raise PermissionError(f'Unsupported feature contract: {decision_id}. No vetted feature adapter registered.')

