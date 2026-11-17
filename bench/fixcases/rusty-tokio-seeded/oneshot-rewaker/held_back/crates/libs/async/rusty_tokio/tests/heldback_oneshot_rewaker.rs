use rusty_tokio::sync::oneshot;
use rusty_tokio::time::{sleep, timeout};
use rusty_tokio::Runtime;
use std::future::Future;
use std::pin::Pin;
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

fn flag() -> (Arc<Flag>, Waker) {
    let flag = Arc::new(Flag(AtomicBool::new(false)));
    (flag.clone(), Waker::from(flag))
}

#[test]
fn send_wakes_the_waker_from_the_latest_poll() {
    let rt = Runtime::new().unwrap();
    rt.block_on(async {
        let (tx, mut rx) = oneshot::channel::<u32>();
        let (first, first_waker) = flag();
        let (second, second_waker) = flag();
        let pending = Pin::new(&mut rx).poll(&mut Context::from_waker(&first_waker));
        assert!(pending.is_pending());
        let pending = Pin::new(&mut rx).poll(&mut Context::from_waker(&second_waker));
        assert!(pending.is_pending());

        tx.send(7).unwrap();

        assert!(
            second.0.load(Ordering::SeqCst),
            "send must wake the waker the receiver was last polled with"
        );
        let _ = first;
        let ready = Pin::new(&mut rx).poll(&mut Context::from_waker(&second_waker));
        assert!(matches!(ready, Poll::Ready(Ok(7))));
    });
}

#[test]
fn a_receiver_moved_to_another_task_after_a_timeout_still_gets_the_value() {
    let rt = Runtime::new().unwrap();
    rt.block_on(async {
        let (tx, mut rx) = oneshot::channel::<u32>();
        assert!(timeout(Duration::from_millis(10), &mut rx).await.is_err());
        let receiver = rusty_tokio::spawn(async move { rx.await });
        sleep(Duration::from_millis(20)).await;
        tx.send(5).unwrap();
        let got = timeout(Duration::from_secs(5), receiver)
            .await
            .expect("receiver task was never woken")
            .unwrap();
        assert_eq!(got, Ok(5));
    });
}
