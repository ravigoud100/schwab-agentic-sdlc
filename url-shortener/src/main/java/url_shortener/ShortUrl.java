package url_shortener;

import jakarta.persistence.*;
import java.time.LocalDateTime;

@Entity
@Table(name = "short_urls")
public class ShortUrl {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, length = 2048)
    private String originalUrl;

    @Column(nullable = false, unique = true, length = 20)
    private String shortCode;

    private Long clickCount = 0L;

    private LocalDateTime createdAt = LocalDateTime.now();

    public ShortUrl() {
    }

    public ShortUrl(String originalUrl, String shortCode) {
        this.originalUrl = originalUrl;
        this.shortCode = shortCode;
    }

    public Long getId() {
        return id;
    }

    public String getOriginalUrl() {
        return originalUrl;
    }

    public String getShortCode() {
        return shortCode;
    }

    public Long getClickCount() {
        return clickCount;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void incrementClickCount() {
        this.clickCount++;
    }
}