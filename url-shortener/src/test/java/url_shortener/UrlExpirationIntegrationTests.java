package url_shortener;

import static org.springframework.http.MediaType.APPLICATION_JSON;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.time.Instant;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

@SpringBootTest
@AutoConfigureMockMvc
class UrlExpirationIntegrationTests {

    private static final Pattern SHORT_CODE_PATTERN = Pattern.compile(
            "\\\"shortCode\\\":\\\"([^\\\"]+)\\\"");

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private UrlRepository repository;

    @BeforeEach
    void cleanDatabase() {
        repository.deleteAll();
    }

    @Test
    void createsShortUrlWithoutExpiration() throws Exception {
        MvcResult result = createUrl("https://example.com/no-expiration", null);

        String shortCode = shortCodeFrom(result);

        mockMvc.perform(get("/api/urls/" + shortCode + "/analytics"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.originalUrl")
                        .value("https://example.com/no-expiration"));
    }

    @Test
    void createsShortUrlWithFutureExpiration() throws Exception {
        MvcResult result = createUrl(
                "https://example.com/future",
                "2099-01-01T00:00:00Z");

        ShortUrl shortUrl = repository.findByShortCode(
                shortCodeFrom(result)).orElseThrow();

        org.junit.jupiter.api.Assertions.assertEquals(
                Instant.parse("2099-01-01T00:00:00Z"),
                shortUrl.getExpiresAt());
    }

    @Test
    void rejectsPastExpiration() throws Exception {
        mockMvc.perform(post("/api/urls")
                        .contentType(APPLICATION_JSON)
                        .content("""
                                {
                                  "originalUrl": "https://example.com/past",
                                  "expiresAt": "2020-01-01T00:00:00Z"
                                }
                                """))
                .andExpect(status().isBadRequest());
    }

    @Test
    void redirectsActiveUrlWithFound() throws Exception {
        MvcResult result = createUrl("https://example.com/active", null);
        String shortCode = shortCodeFrom(result);

        mockMvc.perform(get("/" + shortCode))
                .andExpect(status().isFound())
                .andExpect(header().string(
                        "Location", "https://example.com/active"));

        mockMvc.perform(get("/api/urls/" + shortCode + "/analytics"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.totalClicks").value(1));
    }

    @Test
    void returnsGoneForExpiredUrl() throws Exception {
        ShortUrl shortUrl = new ShortUrl(
                "https://example.com/expired", "expired01");
        shortUrl.setExpiresAt(Instant.parse("2020-01-01T00:00:00Z"));
        repository.save(shortUrl);

        mockMvc.perform(get("/expired01"))
                .andExpect(status().isGone());
    }

    @Test
    void returnsNotFoundForUnknownShortCode() throws Exception {
        mockMvc.perform(get("/does-not-exist"))
                .andExpect(status().isNotFound());
    }

    @Test
    void expiredUrlDoesNotIncreaseClickCount() throws Exception {
        ShortUrl shortUrl = new ShortUrl(
                "https://example.com/no-click", "noclick1");
        shortUrl.setExpiresAt(Instant.parse("2020-01-01T00:00:00Z"));
        repository.save(shortUrl);

        mockMvc.perform(get("/noclick1"))
                .andExpect(status().isGone());

        mockMvc.perform(get("/api/urls/noclick1/analytics"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.totalClicks").value(0));
    }

    private MvcResult createUrl(String originalUrl, String expiresAt)
            throws Exception {
        String expiration = expiresAt == null
                ? ""
                : ",\"expiresAt\":\"" + expiresAt + "\"";
        return mockMvc.perform(post("/api/urls")
                        .contentType(APPLICATION_JSON)
                        .content("{\"originalUrl\":\"" + originalUrl
                                + "\"" + expiration + "}"))
                .andExpect(status().isCreated())
                .andReturn();
    }

    private String shortCodeFrom(MvcResult result) throws Exception {
        Matcher matcher = SHORT_CODE_PATTERN.matcher(
                result.getResponse().getContentAsString());
        if (!matcher.find()) {
            throw new AssertionError("shortCode missing from response");
        }
        return matcher.group(1);
    }
}
