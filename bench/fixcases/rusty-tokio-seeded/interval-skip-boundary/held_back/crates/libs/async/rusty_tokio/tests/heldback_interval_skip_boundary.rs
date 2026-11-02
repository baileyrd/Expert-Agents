use rusty_tokio::time::{self, interval_at, Instant, MissedTickBehavior};
use rusty_tokio::Builder;
use std::future::Future;
use std::task::{Context, Waker};
use std::time::Duration;

#[test]
fn skip_after_missing_exactly_whole_periods_waits_for_the_next_one() {
    let rt = Builder::new_current_thread().build().unwrap();
    rt.block_on(async {
        time::pause();
        let period = Duration::from_secs(10);
        let start = Instant::now();
        let mut ticker = interval_at(start, period);
        ticker.set_missed_tick_behavior(MissedTickBehavior::Skip);
        assert_eq!(ticker.tick().await, start);

        // Late by exactly three periods: now == start + 30s, on the grid.
        time::advance(period * 3).await;
        assert_eq!(ticker.tick().await, start + period);

        // The next deadline must be strictly in the future (start + 40s), not now.
        let mut cx = Context::from_waker(Waker::noop());
        let mut next = Box::pin(ticker.tick());
        assert!(next.as_mut().poll(&mut cx).is_pending(), "Skip fired a tick at the current instant");
        drop(next);

        time::advance(period).await;
        assert_eq!(ticker.tick().await, start + period * 4);
    });
}

#[test]
fn skip_from_a_late_start_lands_on_the_grid() {
    let rt = Builder::new_current_thread().build().unwrap();
    rt.block_on(async {
        time::pause();
        let period = Duration::from_secs(5);
        let start = Instant::now();
        let mut ticker = interval_at(start, period);
        ticker.set_missed_tick_behavior(MissedTickBehavior::Skip);
        time::advance(period * 2).await;

        assert_eq!(ticker.tick().await, start);
        time::advance(period).await;
        assert_eq!(ticker.tick().await, start + period * 3);
    });
}
