use rusty_tokio::io::{self, AsyncReadExt, AsyncWriteExt};
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
fn shutdown_wakes_a_reader_parked_on_an_empty_pipe() {
    let rt = Runtime::new().unwrap();
    rt.block_on(async {
        let (mut a, mut b) = io::duplex(64);
        let flag = Arc::new(Flag(AtomicBool::new(false)));
        let waker = Waker::from(flag.clone());
        let mut buf = [0u8; 8];
        let mut read = Box::pin(b.read(&mut buf));
        assert!(read.as_mut().poll(&mut Context::from_waker(&waker)).is_pending());

        a.shutdown().await.unwrap();

        assert!(
            flag.0.load(Ordering::SeqCst),
            "shutdown must wake a reader parked on the other half"
        );
        let eof = read.as_mut().poll(&mut Context::from_waker(&waker));
        assert!(matches!(eof, Poll::Ready(Ok(0))));
        drop(a);
    });
}

#[test]
fn a_reader_task_sees_eof_when_the_peer_shuts_down() {
    let rt = Runtime::new().unwrap();
    rt.block_on(async {
        let (mut a, mut b) = io::duplex(64);
        let reader = rusty_tokio::spawn(async move {
            let mut got = Vec::new();
            b.read_to_end(&mut got).await.map(|_| got)
        });
        sleep(Duration::from_millis(20)).await;
        a.write_all(b"last words").await.unwrap();
        sleep(Duration::from_millis(20)).await;
        a.shutdown().await.unwrap();

        let got = timeout(Duration::from_secs(5), reader)
            .await
            .expect("reader never saw EOF after shutdown")
            .unwrap()
            .unwrap();
        assert_eq!(got, b"last words");
        drop(a);
    });
}
