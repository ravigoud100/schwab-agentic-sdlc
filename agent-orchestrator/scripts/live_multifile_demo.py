"""Live coordinated service/controller demo; uses the shared lifecycle runner."""
import live_implementation_demo as demo

demo.RELATIVE = 'url-shortener/src/main/java/url_shortener/UrlService.java'
demo.APPROVED_PATHS = [demo.RELATIVE, 'url-shortener/src/main/java/url_shortener/UrlController.java']
demo.FEATURE_MARKER = 'getStatus('
demo.BASELINE_MARKER = 'UrlStatusAcceptanceTest'
demo.TEST_RELATIVE = 'url-shortener/src/test/java/url_shortener/UrlStatusAcceptanceTest.java'
demo.REQUIREMENT = (
    'Add GET /api/urls/{shortCode}/status to the existing URL API. '
    'Return HTTP 200 with JSON fields shortCode (string) and expired (boolean). '
    'An unset or future expiration means expired=false; a past expiration means true. '
    'Unknown codes must return HTTP 404. Reading status must never increment clicks. '
    'Add public java.util.Map<String,Object> getStatus(String shortCode) to UrlService '
    'using a read-only transaction, the existing repository lookup, existing 404 behavior, '
    'and Instant.now() to evaluate expiration. Add the annotated controller endpoint '
    'to UrlController, delegating to urlService.getStatus(shortCode). '
    'Preserve existing endpoints and behavior. The method and endpoint do not exist yet. '
    'Create exactly ONE implementation task containing BOTH existing approved files: '
    'url-shortener/src/main/java/url_shortener/UrlService.java and '
    'url-shortener/src/main/java/url_shortener/UrlController.java. '
    'Independent acceptance tests are already provided; do not modify tests or any other files.'
)
demo.TEST = '''package url_shortener;
import java.time.Instant;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.web.servlet.MockMvc;
import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
@SpringBootTest @AutoConfigureMockMvc
class UrlStatusAcceptanceTest {
    @Autowired MockMvc mvc;
    @Autowired UrlRepository repository;
    @Autowired UrlService service;
    @BeforeEach void clean() { repository.deleteAll(); }
    private ShortUrl save(String code, Instant expiry) {
        ShortUrl url = new ShortUrl("https://example.com/" + code, code);
        url.setExpiresAt(expiry);
        url.incrementClickCount();
        return repository.save(url);
    }
    private void verify(String code, boolean expired) throws Exception {
        mvc.perform(get("/api/urls/" + code + "/status"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.shortCode").value(code))
            .andExpect(jsonPath("$.expired").value(expired));
        assertEquals(1L, repository.findByShortCode(code).orElseThrow().getClickCount());
    }
    @Test void activeStatusesDoNotIncrementClicks() throws Exception {
        save("unset1", null); save("future1", Instant.parse("2099-01-01T00:00:00Z"));
        verify("unset1", false); verify("future1", false);
    }
    @Test void expiredStatusDoesNotIncrementClicks() throws Exception {
        save("expired1", Instant.parse("2020-01-01T00:00:00Z")); verify("expired1", true);
    }
    @Test void unknownCodeReturns404() throws Exception {
        mvc.perform(get("/api/urls/missing-code/status")).andExpect(status().isNotFound());
    }
    @Test void serviceProvidesStatusWithoutIncrementingClicks() throws Exception {
        save("service1", null);
        java.lang.reflect.Method method = UrlService.class.getMethod("getStatus", String.class);
        org.springframework.transaction.annotation.Transactional transaction = method.getAnnotation(org.springframework.transaction.annotation.Transactional.class);
        assertNotNull(transaction, "Status service must declare a read-only transaction");
        assertTrue(transaction.readOnly(), "Status transaction must be read-only");
        Object result = method.invoke(service, "service1");
        assertInstanceOf(Map.class, result);
        assertEquals("service1", ((Map<?,?>) result).get("shortCode"));
        assertEquals(false, ((Map<?,?>) result).get("expired"));
        assertEquals(1L, repository.findByShortCode("service1").orElseThrow().getClickCount());
    }
    @Test void optionalFailureProbe() {
        assertFalse(Boolean.getBoolean("demo.forceValidationFailure"), "Deliberate rollback validation probe");
    }
}
'''

if __name__ == '__main__':
    demo.cli()
