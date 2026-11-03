use rusty_tokio::sync::{oneshot, OnceCell};
use rusty_tokio::time::timeout;
use rusty_tokio::Runtime;
use std::future::Future;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::task::{Context, Poll, Wake, Waker};
use std::time::Duration;

struct Flag(AtomicBool);

impl Wake for Flag {
    fn wake(self: Arc<Self>) {
        self.0.store(true, Ordering::SeqCst);
    }
}

/// Spawns a `get_or_init` whose initializer sleeps for an hour, and returns once it is running.
async fn stuck_initializer(cell: Arc<OnceCell<u32>>) -> rusty_tokio::JoinHandle<()> {
    let (started_tx, started_rx) = oneshot::channel();
    let handle = rusty_tokio::spawn(async move {
        cell.get_or_init(|| async move {
            started_tx.send(()).unwrap();
            rusty_tokio::time::sleep(Duration::from_secs(3600)).await;
            1
        })
        .await;
    });
    started_rx.await.unwrap();
    handle
}

#[test]
fn cancelling_the_initializer_wakes_a_parked_waiter() {
    let rt = Runtime::builder().worker_threads(2).build().unwrap();
    rt.block_on(async {
        let cell = Arc::new(OnceCell::<u32>::new());
        let init = stuck_initializer(cell.clone()).await;

        let flag = Arc::new(Flag(AtomicBool::new(false)));
        let waker = Waker::from(flag.clone());
        let mut waiter = Box::pin(cell.get_or_init(|| async { 2 }));
        assert!(waiter.as_mut().poll(&mut Context::from_waker(&waker)).is_pending());

        init.abort();
        assert!(init.await.unwrap_err().is_cancelled());

        assert!(
            flag.0.load(Ordering::SeqCst),
            "a cancelled initializer must wake the callers waiting on it"
        );
        let ready = waiter.as_mut().poll(&mut Context::from_waker(&waker));
        assert!(matches!(ready, Poll::Ready(&2)));
    });
}

#[test]
fn a_waiting_task_takes_over_after_the_initializer_is_cancelled() {
    let rt = Runtime::builder().worker_threads(2).build().unwrap();
    rt.block_on(async {
        let cell = Arc::new(OnceCell::<u32>::new());
        let init = stuck_initializer(cell.clone()).await;

        let waiting = cell.clone();
        let waiter = rusty_tokio::spawn(async move { *waiting.get_or_init(|| async { 2 }).await });
        rusty_tokio::time::sleep(Duration::from_millis(20)).await;
        init.abort();

        let got = timeout(Duration::from_secs(5), waiter)
            .await
            .expect("waiter hung after the initializer was cancelled")
            .unwrap();
        assert_eq!(got, 2);
        assert_eq!(cell.get(), Some(&2));
    });
}
