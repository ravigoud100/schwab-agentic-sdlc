package url_shortener;
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
