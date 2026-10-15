package url_shortener;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.hibernate.validator.constraints.URL;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.net.URI;
import java.util.Map;

@RestController
@RequestMapping("/api/urls")
public class UrlController {

    private final UrlService urlService;

    public UrlController(UrlService urlService) {
        this.urlService = urlService;
    }

    public record CreateUrlRequest(
            @NotBlank
            @Size(max = 2048)
            @URL(protocol = "https")
            String originalUrl
    ) {}

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Map<String, String> createShortUrl(
            @Valid @RequestBody CreateUrlRequest request) {

        ShortUrl shortUrl = urlService.createShortUrl(
                request.originalUrl()
        );

        return Map.of(
                "originalUrl", shortUrl.getOriginalUrl(),
                "shortCode", shortUrl.getShortCode(),
                "shortUrl", "http://localhost:8080/" + shortUrl.getShortCode()
        );
    }
    
    @GetMapping("/{shortCode}/analytics")
public Map<String, Object> getAnalytics(
        @PathVariable String shortCode) {

    ShortUrl shortUrl = urlService.getAnalytics(shortCode);

    return Map.of(
            "shortCode", shortUrl.getShortCode(),
            "originalUrl", shortUrl.getOriginalUrl(),
            "totalClicks", shortUrl.getClickCount(),
            "createdAt", shortUrl.getCreatedAt().toString()
    );
}
}