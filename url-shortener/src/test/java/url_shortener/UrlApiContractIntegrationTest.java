package url_shortener;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import java.time.Instant;

import static org.hamcrest.Matchers.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "app.short-url.base-url=https://schwab.short/"
})
class UrlApiContractIntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private UrlRepository repository;

    @BeforeEach
    void clean() {
        repository.deleteAll();
    }

    @Test
    void createUrlReturnsExpectedJsonContractWithConfiguredBaseUrl() throws Exception {
        mockMvc.perform(post("/api/urls")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "originalUrl": "https://example.com/api-contract"
                                }
                                """))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.originalUrl").value("https://example.com/api-contract"))
                .andExpect(jsonPath("$.shortCode").isString())
                .andExpect(jsonPath("$.shortCode", hasLength(7)))
                .andExpect(jsonPath("$.shortUrl", startsWith("https://schwab.short/")));
    }

    @Test
    void analyticsEndpointReturnsExactJsonFields() throws Exception {
        ShortUrl url = new ShortUrl("https://example.com/contract-analytics", "an001");
        url.incrementClickCount();
        url.incrementClickCount();
        repository.save(url);

        mockMvc.perform(get("/api/urls/an001/analytics"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.shortCode").value("an001"))
                .andExpect(jsonPath("$.originalUrl").value("https://example.com/contract-analytics"))
                .andExpect(jsonPath("$.totalClicks").value(2))
                .andExpect(jsonPath("$.createdAt").isString());
    }

    @Test
    void statusEndpointReturnsExactJsonFields() throws Exception {
        ShortUrl url = new ShortUrl("https://example.com/contract-status", "st001");
        repository.save(url);

        mockMvc.perform(get("/api/urls/st001/status"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.shortCode").value("st001"))
                .andExpect(jsonPath("$.expired").value(false));
    }

    @Test
    void notFoundReturnsRFC7807ProblemDetailStructure() throws Exception {
        mockMvc.perform(get("/api/urls/missing-contract/status"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.status").value(404))
                .andExpect(jsonPath("$.title").value("Not Found"))
                .andExpect(jsonPath("$.detail").value("Short URL not found"));
    }

    @Test
    void expiredReturnsRFC7807ProblemDetailStructure() throws Exception {
        ShortUrl url = new ShortUrl("https://example.com/expired-contract", "exp01");
        url.setExpiresAt(Instant.parse("2020-01-01T00:00:00Z"));
        repository.save(url);

        mockMvc.perform(get("/exp01"))
                .andExpect(status().isGone())
                .andExpect(jsonPath("$.status").value(410))
                .andExpect(jsonPath("$.title").value("Gone"))
                .andExpect(jsonPath("$.detail").value("Short URL has expired"));
    }

    @Test
    void badRequestReturnsRFC7807ProblemDetailStructure() throws Exception {
        mockMvc.perform(post("/api/urls")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "originalUrl": "https://example.com/invalid",
                                  "expiresAt": "2020-01-01T00:00:00Z"
                                }
                                """))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.status").value(400))
                .andExpect(jsonPath("$.title").value("Bad Request"))
                .andExpect(jsonPath("$.detail").value("Expiration must be in the future"));
    }

    @Test
    void validationErrorReturnsRFC7807ProblemDetailStructure() throws Exception {
        mockMvc.perform(post("/api/urls")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "originalUrl": "not-a-valid-url"
                                }
                                """))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.status").value(400))
                .andExpect(jsonPath("$.title").value("Bad Request"))
                .andExpect(jsonPath("$.detail").value("Invalid request content."));
    }

    @Test
    void exceptionHandlerDirectCoverage() {
        GlobalExceptionHandler handler = new GlobalExceptionHandler();
        var resp1 = handler.handleIllegalStateException(new IllegalStateException("Service error"));
        org.junit.jupiter.api.Assertions.assertEquals(503, resp1.getStatusCode().value());
        org.junit.jupiter.api.Assertions.assertEquals("Service error", resp1.getBody().getDetail());

        var resp2 = handler.handleResponseStatusException(new org.springframework.web.server.ResponseStatusException(
                org.springframework.http.HttpStatus.NOT_FOUND, "Not found reason"));
        org.junit.jupiter.api.Assertions.assertEquals(404, resp2.getStatusCode().value());
        org.junit.jupiter.api.Assertions.assertEquals("Not found reason", resp2.getBody().getDetail());
    }
}
