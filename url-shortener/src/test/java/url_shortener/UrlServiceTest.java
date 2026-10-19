package url_shortener;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneId;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class UrlServiceTest {

    @Mock
    private UrlRepository repository;

    private static final Instant T0 = Instant.parse("2026-10-18T12:00:00Z");
    private Clock fixedClock;
    private UrlService urlService;

    @BeforeEach
    void setUp() {
        fixedClock = Clock.fixed(T0, ZoneId.of("UTC"));
        urlService = new UrlService(repository, fixedClock);
    }

    @Test
    void createShortUrlWithoutExpirationSucceeds() {
        when(repository.existsByShortCode(anyString())).thenReturn(false);
        when(repository.saveAndFlush(any(ShortUrl.class))).thenAnswer(inv -> inv.getArgument(0));

        ShortUrl created = urlService.createShortUrl("https://example.com/test", null);

        assertNotNull(created);
        assertEquals("https://example.com/test", created.getOriginalUrl());
        assertNull(created.getExpiresAt());
        verify(repository, times(1)).saveAndFlush(any(ShortUrl.class));
    }

    @Test
    void rejectsExpirationInPastOrExactlyNow() {
        // Expired in past
        ResponseStatusException pastEx = assertThrows(ResponseStatusException.class, () ->
                urlService.createShortUrl("https://example.com/past", T0.minusSeconds(60)));
        assertEquals(HttpStatus.BAD_REQUEST, pastEx.getStatusCode());
        assertEquals("Expiration must be in the future", pastEx.getReason());

        // Exactly at current clock time T0
        ResponseStatusException nowEx = assertThrows(ResponseStatusException.class, () ->
                urlService.createShortUrl("https://example.com/now", T0));
        assertEquals(HttpStatus.BAD_REQUEST, nowEx.getStatusCode());
        assertEquals("Expiration must be in the future", nowEx.getReason());

        verify(repository, never()).saveAndFlush(any());
    }

    @Test
    void retriesOnShortCodeCollisionAndSucceeds() {
        // First code collides, second code is unique
        when(repository.existsByShortCode(anyString()))
                .thenReturn(true)
                .thenReturn(false);
        when(repository.saveAndFlush(any(ShortUrl.class))).thenAnswer(inv -> inv.getArgument(0));

        ShortUrl created = urlService.createShortUrl("https://example.com/retry", null);

        assertNotNull(created);
        verify(repository, times(2)).existsByShortCode(anyString());
        verify(repository, times(1)).saveAndFlush(any(ShortUrl.class));
    }

    @Test
    void exhaustsRetriesWhenAllShortCodesCollide() {
        // All 10 attempts collide
        when(repository.existsByShortCode(anyString())).thenReturn(true);

        IllegalStateException ex = assertThrows(IllegalStateException.class, () ->
                urlService.createShortUrl("https://example.com/exhausted", null));

        assertEquals("Unable to generate unique short URL", ex.getMessage());
        verify(repository, times(10)).existsByShortCode(anyString());
        verify(repository, never()).saveAndFlush(any());
    }

    @Test
    void retriesOnConcurrentUniqueConstraintViolation() {
        // existsByShortCode returns false, but saveAndFlush throws DataIntegrityViolationException
        // because another thread inserted the same short code.
        when(repository.existsByShortCode(anyString()))
                .thenReturn(false)  // initial check passes
                .thenReturn(true)   // in catch block: confirms actual unique conflict
                .thenReturn(false); // next attempt: check passes

        when(repository.saveAndFlush(any(ShortUrl.class)))
                .thenThrow(new DataIntegrityViolationException("Duplicate key"))
                .thenAnswer(inv -> inv.getArgument(0));

        ShortUrl created = urlService.createShortUrl("https://example.com/concurrent", null);

        assertNotNull(created);
        verify(repository, times(2)).saveAndFlush(any(ShortUrl.class));
    }

    @Test
    void rethrowsDataIntegrityViolationIfNotUniqueCollision() {
        when(repository.existsByShortCode(anyString())).thenReturn(false);
        when(repository.saveAndFlush(any(ShortUrl.class)))
                .thenThrow(new DataIntegrityViolationException("Not a unique key collision"));

        assertThrows(DataIntegrityViolationException.class, () ->
                urlService.createShortUrl("https://example.com/error", null));
    }

    @Test
    void deterministicExpirationBoundaries() {
        Instant expiration = T0.plusSeconds(3600);
        ShortUrl url = new ShortUrl("https://example.com/boundary", "bound01");
        url.setExpiresAt(expiration);

        when(repository.findByShortCode("bound01")).thenReturn(Optional.of(url));

        // 1. At T0 (well before expiration) -> active
        Map<String, Object> statusBefore = urlService.getStatus("bound01");
        assertFalse((Boolean) statusBefore.get("expired"));

        // 2. Exactly at expiration timestamp -> active (now.isAfter(expiresAt) is false)
        UrlService serviceAtExact = new UrlService(repository, Clock.fixed(expiration, ZoneId.of("UTC")));
        Map<String, Object> statusExact = serviceAtExact.getStatus("bound01");
        assertFalse((Boolean) statusExact.get("expired"));

        // 3. One millisecond past expiration -> expired (now.isAfter(expiresAt) is true)
        UrlService servicePast = new UrlService(repository, Clock.fixed(expiration.plusMillis(1), ZoneId.of("UTC")));
        Map<String, Object> statusPast = servicePast.getStatus("bound01");
        assertTrue((Boolean) statusPast.get("expired"));

        ResponseStatusException ex = assertThrows(ResponseStatusException.class, () ->
                servicePast.getOriginalUrl("bound01"));
        assertEquals(HttpStatus.GONE, ex.getStatusCode());
        assertEquals("Short URL has expired", ex.getReason());
    }

    @Test
    void getOriginalUrlIncrementsClickCountAtomically() {
        ShortUrl url = new ShortUrl("https://example.com/click", "click01");
        when(repository.findByShortCode("click01")).thenReturn(Optional.of(url));

        ShortUrl result = urlService.getOriginalUrl("click01");

        assertNotNull(result);
        assertEquals(1L, result.getClickCount());
        verify(repository, times(1)).incrementClickCountByShortCode("click01");
    }

    @Test
    void getOriginalUrlThrowsNotFoundForMissingCode() {
        when(repository.findByShortCode("missing")).thenReturn(Optional.empty());

        ResponseStatusException ex = assertThrows(ResponseStatusException.class, () ->
                urlService.getOriginalUrl("missing"));
        assertEquals(HttpStatus.NOT_FOUND, ex.getStatusCode());
        assertEquals("Short URL not found", ex.getReason());
        verify(repository, never()).incrementClickCountByShortCode(anyString());
    }
}
