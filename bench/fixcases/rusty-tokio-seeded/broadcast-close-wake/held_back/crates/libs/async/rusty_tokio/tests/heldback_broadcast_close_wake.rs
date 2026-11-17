use rusty_tokio::sync::broadcast::{self, RecvError};
use rusty_tokio::time::{sleep, timeout};
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

#[test]
fn dropping_the_last_sender_wakes_a_parked_receiver() {
    let rt = Runtime::new().unwrap();
    rt.block_on(async {
        let (tx, mut rx) = broadcast::channel::<i32>(4);
        let other = tx.clone();
        let flag = Arc::new(Flag(AtomicBool::new(false)));
        let waker = Waker::from(flag.clone());
        let mut recv = Box::pin(rx.recv());
        assert!(recv.as_mut().poll(&mut Context::from_waker(&waker)).is_pending());

        drop(other);
        drop(tx);

        assert!(
            flag.0.load(Ordering::SeqCst),
            "the last sender's drop must wake every parked receiver"
        );
        let closed = recv.as_mut().poll(&mut Context::from_waker(&waker));
        assert!(matches!(closed, Poll::Ready(Err(RecvError::Closed))));
    });
}

/// Fails the binary if the test deadlocks, rather than hanging the run.
fn watchdog(limit: Duration) {
    std::thread::spawn(move || {
        std::thread::sleep(limit);
        eprintln!("watchdog: test still running after {limit:?}, likely deadlocked");
        std::process::exit(2);
    });
}

#[test]
fn a_receiver_task_sees_closed_when_the_last_sender_drops() {
    watchdog(Duration::from_secs(30));
    let rt = Runtime::new().unwrap();
    rt.block_on(async {
        let (tx, mut rx) = broadcast::channel::<i32>(4);
        let receiver = rusty_tokio::spawn(async move { rx.recv().await });
        sleep(Duration::from_millis(20)).await;
        drop(tx);
        let got = timeout(Duration::from_secs(5), receiver)
            .await
            .expect("receiver still parked after the last sender dropped")
            .unwrap();
        assert_eq!(got, Err(RecvError::Closed));
    });
}
