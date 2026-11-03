use rusty_tokio::io::{AsyncReadExt, BufReader};
use rusty_tokio::Runtime;

#[test]
fn a_large_read_after_a_small_one_returns_the_buffered_bytes_first() {
    let rt = Runtime::new().unwrap();
    rt.block_on(async {
        let data: &[u8] = b"abcdefghij";
        let mut reader = BufReader::with_capacity(4, data);
        let mut one = [0u8; 1];
        reader.read_exact(&mut one).await.unwrap();
        assert_eq!(&one, b"a");

        let mut big = [0u8; 16];
        let n = reader.read(&mut big).await.unwrap();
        assert_eq!(&big[..n], b"bcd");
    });
}

#[test]
fn mixed_read_sizes_never_lose_or_reorder_bytes() {
    let rt = Runtime::new().unwrap();
    rt.block_on(async {
        let data: Vec<u8> = (0..=255u8).cycle().take(1000).collect();
        let mut reader = BufReader::with_capacity(16, &data[..]);
        let mut out = Vec::new();
        let sizes = [1usize, 40, 3, 16, 2, 64, 7, 100];
        let mut i = 0;
        loop {
            let mut buf = vec![0u8; sizes[i % sizes.len()]];
            let n = reader.read(&mut buf).await.unwrap();
            if n == 0 {
                break;
            }
            out.extend_from_slice(&buf[..n]);
            i += 1;
        }
        assert_eq!(out, data);
    });
}
