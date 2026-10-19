package url_shortener;

import org.springframework.stereotype.Service;
import java.security.SecureRandom;
import java.time.Clock;
import java.time.Instant;
import java.util.Map;

@Service
public class UrlService {

    private final UrlRepository repository;
    private final Clock clock;

    private static final String CHARACTERS =
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";

    private final SecureRandom random = new SecureRandom();

    @org.springframework.beans.factory.annotation.Autowired
    public UrlService(UrlRepository repository) {
        this(repository, java.time.Clock.systemUTC());
    }

    public UrlService(UrlRepository repository, java.time.Clock clock) {
        this.repository = repository;
        this.clock = clock;
    }

    public ShortUrl createShortUrl(
            String originalUrl,
            java.time.Instant expiresAt
    ) {

        if (expiresAt != null &&
                !expiresAt.isAfter(clock.instant())) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.BAD_REQUEST,
                    "Expiration must be in the future"
            );
        }

        int attempts = 0;
        while (attempts < 10) {
            String shortCode = generateShortCode();
            attempts++;

            if (repository.existsByShortCode(shortCode)) {
                continue;
            }

            ShortUrl shortUrl = new ShortUrl(originalUrl, shortCode);
            shortUrl.setExpiresAt(expiresAt);

            try {
                return repository.saveAndFlush(shortUrl);
            } catch (org.springframework.dao.DataIntegrityViolationException ex) {
                // Retry only actual unique short-code collisions
                if (repository.existsByShortCode(shortCode)) {
                    continue;
                }
                throw ex;
            }
        }

        throw new IllegalStateException("Unable to generate unique short URL");
    }

    String generateShortCode() {

        StringBuilder code = new StringBuilder();

        for (int i = 0; i < 7; i++) {
            int index = random.nextInt(CHARACTERS.length());
            code.append(CHARACTERS.charAt(index));
        }

        return code.toString();
    }

    @org.springframework.transaction.annotation.Transactional
    public ShortUrl getOriginalUrl(String shortCode) {

        ShortUrl shortUrl = repository.findByShortCode(shortCode)
                .orElseThrow(() ->
                        new org.springframework.web.server.ResponseStatusException(
                                org.springframework.http.HttpStatus.NOT_FOUND,
                                "Short URL not found"
                        )
                );

        if (shortUrl.isExpired(clock.instant())) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.GONE,
                    "Short URL has expired"
            );
        }

        repository.incrementClickCountByShortCode(shortCode);
        shortUrl.incrementClickCount();

        return shortUrl;
    }

    @org.springframework.transaction.annotation.Transactional(readOnly = true)
    public ShortUrl getAnalytics(String shortCode) {

        return repository.findByShortCode(shortCode)
                .orElseThrow(() ->
                        new org.springframework.web.server.ResponseStatusException(
                                org.springframework.http.HttpStatus.NOT_FOUND,
                                "Short URL not found"
                        )
                );
    }

    @org.springframework.transaction.annotation.Transactional(readOnly = true)
    public java.util.Map<String, Object> getStatus(String shortCode) {

        ShortUrl shortUrl = repository.findByShortCode(shortCode)
                .orElseThrow(() ->
                        new org.springframework.web.server.ResponseStatusException(
                                org.springframework.http.HttpStatus.NOT_FOUND,
                                "Short URL not found"
                        )
                );

        return Map.of(
                "shortCode", shortUrl.getShortCode(),
                "expired", shortUrl.isExpired(clock.instant())
        );
    }
}
