use rusty_tokio::time::{self, timeout, timeout_at, Instant};
use rusty_tokio::{Builder, Runtime};
use std::time::Duration;

#[test]
fn a_ready_future_wins_over_a_zero_timeout() {
    let rt = Runtime::new().unwrap();
    rt.block_on(async {
        assert_eq!(timeout(Duration::ZERO, async { 5 }).await, Ok(5));
    });
}

#[test]
fn a_ready_future_wins_over_a_deadline_already_in_the_past() {
    let rt = Runtime::new().unwrap();
    rt.block_on(async {
        let past = Instant::now() - Duration::from_millis(10);
        assert_eq!(timeout_at(past, std::future::ready(7)).await, Ok(7));
    });
}

#[test]
fn a_future_that_completes_at_the_deadline_returns_its_value() {
    let rt = Builder::new_current_thread().build().unwrap();
    rt.block_on(async {
        time::pause();
        let period = Duration::from_secs(1);
        let inner = time::sleep(period);
        let guarded = timeout(period, async move {
            inner.await;
            "done"
        });
        time::advance(period).await;
        assert_eq!(guarded.await, Ok("done"));
    });
}
