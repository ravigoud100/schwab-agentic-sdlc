package url_shortener;

import org.springframework.stereotype.Component;

@Component
public class UrlHealthComponent {
    public String status() {
        return "ready";
    }
}
