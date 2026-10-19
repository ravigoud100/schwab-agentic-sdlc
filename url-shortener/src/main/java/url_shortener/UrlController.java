package url_shortener;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.hibernate.validator.constraints.URL;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.time.Instant;
import java.util.Map;

@RestController
@RequestMapping("/api/urls")
public class UrlController {

    private final UrlService urlService;
    private final String baseUrl;

    public UrlController(UrlService urlService) {
        this(urlService, "http://localhost:8080/");
    }

    @org.springframework.beans.factory.annotation.Autowired
    public UrlController(
            UrlService urlService,
            @Value("${app.short-url.base-url:http://localhost:8080/}") String baseUrl
    ) {
        this.urlService = urlService;
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl : baseUrl + "/";
    }

    public record CreateUrlRequest(
            @NotBlank
            @Size(max = 2048)
            @URL(protocol = "https")
            String originalUrl,
            Instant expiresAt
    ) {}

    public record CreateUrlResponse(
            String originalUrl,
            String shortCode,
            String shortUrl
    ) {}

    public record UrlAnalyticsResponse(
            String shortCode,
            String originalUrl,
            Long totalClicks,
            String createdAt
    ) {}

    public record UrlStatusResponse(
            String shortCode,
            boolean expired
    ) {}

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public CreateUrlResponse createShortUrl(
            @Valid @RequestBody CreateUrlRequest request) {

        ShortUrl shortUrl = urlService.createShortUrl(
                request.originalUrl(),
                request.expiresAt()
        );

        return new CreateUrlResponse(
                shortUrl.getOriginalUrl(),
                shortUrl.getShortCode(),
                this.baseUrl + shortUrl.getShortCode()
        );
    }

    @GetMapping("/{shortCode}/analytics")
    public UrlAnalyticsResponse getAnalytics(
            @PathVariable String shortCode) {

        ShortUrl shortUrl = urlService.getAnalytics(shortCode);

        return new UrlAnalyticsResponse(
                shortUrl.getShortCode(),
                shortUrl.getOriginalUrl(),
                shortUrl.getClickCount(),
                shortUrl.getCreatedAt().toString()
        );
    }

    @GetMapping("/{shortCode}/status")
    public UrlStatusResponse getStatus(@PathVariable String shortCode) {

        Map<String, Object> status = urlService.getStatus(shortCode);

        return new UrlStatusResponse(
                (String) status.get("shortCode"),
                (Boolean) status.get("expired")
        );
    }
}
