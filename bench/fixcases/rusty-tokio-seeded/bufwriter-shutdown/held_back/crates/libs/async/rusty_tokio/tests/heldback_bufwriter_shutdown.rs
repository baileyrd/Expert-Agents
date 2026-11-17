use rusty_tokio::io::{self, AsyncReadExt, AsyncWriteExt, BufWriter};
use rusty_tokio::Runtime;

#[test]
fn shutdown_flushes_buffered_bytes_before_closing() {
    let rt = Runtime::new().unwrap();
    rt.block_on(async {
        let (a, mut b) = io::duplex(64);
        let mut writer = BufWriter::new(a);
        writer.write_all(b"hello").await.unwrap();
        writer.shutdown().await.unwrap();

        let mut got = Vec::new();
        b.read_to_end(&mut got).await.unwrap();
        assert_eq!(got, b"hello");
        drop(writer);
    });
}

#[test]
fn a_buf_stream_shutdown_also_flushes() {
    let rt = Runtime::new().unwrap();
    rt.block_on(async {
        let (a, mut b) = io::duplex(64);
        let mut stream = io::BufStream::new(a);
        stream.write_all(b"bye").await.unwrap();
        stream.shutdown().await.unwrap();

        let mut got = Vec::new();
        b.read_to_end(&mut got).await.unwrap();
        assert_eq!(got, b"bye");
        drop(stream);
    });
}
