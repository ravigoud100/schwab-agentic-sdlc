package url_shortener;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.lang.reflect.Method;
import java.time.Clock;
import java.time.Instant;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.*;

@SpringBootTest
class UrlDatabaseCollisionIntegrationTest {

    @Autowired
    private UrlRepository repository;

    @Autowired
    private Clock clock;

    @BeforeEach
    void cleanUp() {
        repository.deleteAll();
    }

    @Test
    void actualDatabaseUniqueConstraintConflictRecoversWithoutRollbackOnlyException() {
        // 1. Pre-insert a row with shortCode "DUPLIC8" into the real H2 database
        ShortUrl existing = new ShortUrl("https://example.com/existing", "DUPLIC8");
        repository.saveAndFlush(existing);

        // 2. Create a proxy repository that simulates a concurrent race condition:
        // existsByShortCode returns false on first check (so saveAndFlush is actually attempted against H2),
        // but returns real database value true when queried inside the catch block to verify conflict.
        AtomicInteger existsCallCount = new AtomicInteger(0);

        UrlRepository raceRepository = (UrlRepository) java.lang.reflect.Proxy.newProxyInstance(
                UrlRepository.class.getClassLoader(),
                new Class<?>[]{UrlRepository.class},
                (proxy, method, args) -> {
                    if ("existsByShortCode".equals(method.getName()) && "DUPLIC8".equals(args[0])) {
                        if (existsCallCount.incrementAndGet() == 1) {
                            // Race condition: Thread thinks short code is available
                            return false;
                        }
                    }
                    try {
                        return method.invoke(repository, args);
                    } catch (java.lang.reflect.InvocationTargetException ite) {
                        throw ite.getTargetException();
                    }
                }
        );

        // 3. Subclass UrlService to return "DUPLIC8" on attempt 1 and "RESOLV8" on attempt 2
        AtomicInteger attemptCounter = new AtomicInteger(0);
        UrlService raceService = new UrlService(raceRepository, clock) {
            @Override
            String generateShortCode() {
                if (attemptCounter.incrementAndGet() == 1) {
                    return "DUPLIC8";
                }
                return "RESOLV8";
            }
        };

        // 4. Execute createShortUrl: Attempt 1 will trigger an ACTUAL H2 unique-constraint violation,
        // and attempt 2 must succeed without throwing RollbackException / TransactionSystemException.
        ShortUrl created = raceService.createShortUrl("https://example.com/race-resolved", null);

        assertNotNull(created);
        assertEquals("RESOLV8", created.getShortCode());
        assertEquals("https://example.com/race-resolved", created.getOriginalUrl());

        // 5. Verify both records exist in the real database
        assertTrue(repository.existsByShortCode("DUPLIC8"));
        assertTrue(repository.existsByShortCode("RESOLV8"));
        assertEquals("https://example.com/existing", repository.findByShortCode("DUPLIC8").orElseThrow().getOriginalUrl());
        assertEquals("https://example.com/race-resolved", repository.findByShortCode("RESOLV8").orElseThrow().getOriginalUrl());
    }

    @Test
    void createShortUrlIsNotAnnotatedWithTransactionalToPreventRollbackOnlyTraps() throws Exception {
        Method method = UrlService.class.getMethod("createShortUrl", String.class, Instant.class);
        assertNull(method.getAnnotation(Transactional.class),
                "createShortUrl must NOT be @Transactional; failed inserts must not mark transactions rollback-only");
    }

    @Test
    void atomicClickIncrementsPreserveRedirectAndNeverIncrementExpiredOrMissingUrls() {
        UrlService service = new UrlService(repository, clock);

        // 1. Missing URL throws 404 and does not increment anything
        assertThrows(ResponseStatusException.class, () -> service.getOriginalUrl("missing-url-code"));

        // 2. Expired URL throws 410 and clickCount remains strictly 0
        ShortUrl expired = new ShortUrl("https://example.com/expired-never-click", "EXP00");
        expired.setExpiresAt(Instant.parse("2020-01-01T00:00:00Z"));
        repository.saveAndFlush(expired);

        assertEquals(0L, repository.findByShortCode("EXP00").orElseThrow().getClickCount());

        ResponseStatusException goneEx = assertThrows(ResponseStatusException.class, () ->
                service.getOriginalUrl("EXP00"));
        assertEquals(410, goneEx.getStatusCode().value());

        // Verify clickCount in DB is STILL 0
        assertEquals(0L, repository.findByShortCode("EXP00").orElseThrow().getClickCount(),
                "Click count must NOT be incremented for expired URLs");

        // 3. Valid active URL increments clickCount exactly by 1
        ShortUrl active = new ShortUrl("https://example.com/active-click", "ACT01");
        active.setExpiresAt(Instant.now(clock).plusSeconds(3600));
        repository.saveAndFlush(active);

        assertEquals(0L, repository.findByShortCode("ACT01").orElseThrow().getClickCount());

        ShortUrl retrieved = service.getOriginalUrl("ACT01");
        assertEquals("https://example.com/active-click", retrieved.getOriginalUrl());
        assertEquals(1L, retrieved.getClickCount());

        // Verify in DB that atomic update persisted
        assertEquals(1L, repository.findByShortCode("ACT01").orElseThrow().getClickCount(),
                "Click count must be atomically incremented in the database for valid active URLs");
    }
}
