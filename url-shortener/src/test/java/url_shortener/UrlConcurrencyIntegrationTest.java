package url_shortener;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.web.servlet.MockMvc;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
class UrlConcurrencyIntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private UrlRepository repository;

    @BeforeEach
    void clean() {
        repository.deleteAll();
    }

    @Test
    void concurrentRedirectsMaintainAccurateAtomicClickCount() throws Exception {
        ShortUrl url = new ShortUrl("https://example.com/concurrent-clicks", "conc01");
        repository.save(url);

        int totalRequests = 50;
        int threadPoolSize = 10;
        ExecutorService executor = Executors.newFixedThreadPool(threadPoolSize);
        CountDownLatch startLatch = new CountDownLatch(1);
        CountDownLatch doneLatch = new CountDownLatch(totalRequests);
        AtomicInteger successfulRedirects = new AtomicInteger(0);

        for (int i = 0; i < totalRequests; i++) {
            executor.submit(() -> {
                try {
                    startLatch.await();
                    mockMvc.perform(get("/conc01"))
                            .andExpect(status().isFound());
                    successfulRedirects.incrementAndGet();
                } catch (Exception e) {
                    e.printStackTrace();
                } finally {
                    doneLatch.countDown();
                }
            });
        }

        // Release all threads simultaneously
        startLatch.countDown();
        boolean completed = doneLatch.await(10, TimeUnit.SECONDS);
        executor.shutdown();

        assertEquals(true, completed, "All concurrent requests should complete within timeout");
        assertEquals(totalRequests, successfulRedirects.get(), "All 50 redirects must succeed");

        // Verify that the atomic database update recorded exactly 50 clicks without any lost updates
        mockMvc.perform(get("/api/urls/conc01/analytics"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.totalClicks").value(50));
    }
}
