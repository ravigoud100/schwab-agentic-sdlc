package url_shortener;

import org.springframework.stereotype.Service;
import java.security.SecureRandom;
import java.time.Instant;
import java.util.Map;

@Service
public class UrlService {

    private final UrlRepository repository;

    private static final String CHARACTERS =
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";

    private final SecureRandom random = new SecureRandom();

    public UrlService(UrlRepository repository) {
        this.repository = repository;
    }

    public ShortUrl createShortUrl(
            String originalUrl,
            java.time.Instant expiresAt
    ) {

        String shortCode;
        int attempts = 0;

        do {
            shortCode = generateShortCode();
            attempts++;

            if (attempts >= 10) {
                throw new IllegalStateException(
                        "Unable to generate unique short URL");
            }

        } while (repository.existsByShortCode(shortCode));

        ShortUrl shortUrl = new ShortUrl(originalUrl, shortCode);

        if (expiresAt != null &&
                !expiresAt.isAfter(java.time.Instant.now())) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.BAD_REQUEST,
                    "Expiration must be in the future"
            );
        }

        shortUrl.setExpiresAt(expiresAt);

        return repository.save(shortUrl);
    }

    private String generateShortCode() {

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

    if (shortUrl.isExpired(Instant.now())) {
        throw new org.springframework.web.server.ResponseStatusException(
                org.springframework.http.HttpStatus.GONE,
                "Short URL has expired"
        );
    }

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
            "expired", shortUrl.isExpired(Instant.now())
    );
}
}
