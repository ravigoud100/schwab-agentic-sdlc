"""Constrained expiration specification generation plus a trusted JUnit renderer.

Deliberately bounded to the reviewed custom-expiration profile. No arbitrary Java,
annotations, assertion bodies, paths, build settings or test exclusions from models.
"""
import json
from governance.semantic_plan import custom_expiration_contract,digest

CASES={o['id']:o['spec'] for o in custom_expiration_contract()['obligations'] if o['kind']=='test'}


def generate_spec(model,context,scope):
    expected={'cases':[{'obligation_id':key,'spec':value} for key,value in CASES.items()]}
    validate_spec(expected,context['obligation_contract'])
    prompt=f'''Generate ONLY JSON test specifications, not source code.
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
    response=model.invoke(prompt,stage='test_generation')
    payload=json.loads(response.text);validate_spec(payload,context['obligation_contract'])
    return payload,prompt,response.text


def validate_spec(payload,contract):
    # Fail closed for unknown profiles rather than silently reusing expiration tests.
    if contract!=custom_expiration_contract():raise ValueError('No vetted test renderer for this contract')
    if not isinstance(payload,dict) or set(payload)!={'cases'} or not isinstance(payload['cases'],list):raise ValueError('Invalid test specification')
    seen=set()
    for case in payload['cases']:
        if not isinstance(case,dict) or set(case)!={'obligation_id','spec'}:raise ValueError('Arbitrary test changes are prohibited')
        oid=case['obligation_id']
        if not isinstance(oid,str) or oid not in CASES or oid in seen or digest(case['spec'])!=digest(CASES[oid]):raise ValueError('Missing, duplicate, unknown or weakened test obligation')
        seen.add(oid)
    if seen!=set(CASES):raise ValueError('All four expiration test obligations are required')


def render(payload,contract):
    validate_spec(payload,contract)
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
        when(repository.save(any(ShortUrl.class))).thenAnswer(call -> call.getArgument(0));
        service = new UrlService(repository, Clock.fixed(NOW, ZoneOffset.UTC));
    }
    @Test void futureExpiration() {
        Instant future = NOW.plusSeconds(3600);
        ShortUrl url = service.createShortUrl("https://example.com/future", future);
        assertEquals(future, url.getExpiresAt());
        assertFalse(url.isExpired(NOW));
        verify(repository).save(url);
    }
    @Test void pastExpiration() { reject(NOW.minusSeconds(3600)); }
    @Test void equalNowExpiration() { reject(NOW); }
    private void reject(Instant value) {
        ResponseStatusException failure = assertThrows(ResponseStatusException.class,
            () -> service.createShortUrl("https://example.com/reject", value));
        assertEquals(HttpStatus.BAD_REQUEST, failure.getStatusCode());
        verify(repository, never()).save(any(ShortUrl.class));
    }
    @Test void missingExpiration() {
        ShortUrl url = service.createShortUrl("https://example.com/missing", null);
        assertNull(url.getExpiresAt());
        assertFalse(url.isExpired(NOW.plusSeconds(315360000)));
        verify(repository).save(url);
    }
}
'''.encode()
