package url_shortener;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class UrlHealthComponentTest {
    @Test
    void statusIsReady() {
        assertEquals("ready", new UrlHealthComponent().status());
    }
}
