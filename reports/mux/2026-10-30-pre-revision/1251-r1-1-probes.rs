//! Review probes (scratch only).
#![cfg(target_os = "linux")]
mod common;
use common::{Client, block_for, start_server};
use rusty_mux_proto::{Entry, Features, Message, Takeover};

const TAKEOVER: Features = Features::CHECKPOINT_V6
    .union(Features::INSPECT)
    .union(Features::LABELS)
    .union(Features::TAKEOVER);

fn takeover_of(c: &Client) -> Takeover {
    Takeover { client: c.id(), token: c.token.expect("a token") }
}

/// Second reconnect, before the client sent any request on the first
/// reconnect's connection, loses its Welcome. The client still holds the
/// token it just presented.
#[test]
fn probe_second_lost_welcome_after_an_idle_reconnect() {
    let path = start_server();
    let mut old = Client::connect_with(&path, TAKEOVER, None);
    let block = block_for(&mut old); // requests: confirms t1
    let t1 = takeover_of(&old);
    let mut second = Client::taking_over(&path, TAKEOVER, None, Some(t1));
    assert_eq!(second.id(), t1.client);
    second.wait_ready(block); // reads only, no request
    let t2 = takeover_of(&second);
    // Takeover with t2; its Welcome (t3) is lost.
    let lost = Client::taking_over(&path, TAKEOVER, None, Some(t2));
    assert_eq!(lost.id(), t1.client);
    drop(lost);
    let retry = Client::taking_over(&path, TAKEOVER, None, Some(t2));
    assert_eq!(retry.id(), t1.client, "the token the client presented and still holds");
}

/// A block connection hung up without Detach (what the TUI does when a
/// pane leaves the view) keeps its lease for the grace.
#[test]
fn probe_hung_up_pane_keeps_lease() {
    let path = start_server();
    let mut pane = Client::connect_with(&path, TAKEOVER, None);
    let block = block_for(&mut pane);
    drop(pane);
    let mut agent = Client::connect_with(&path, TAKEOVER, None);
    agent.request(|req| Message::Attach { req, block });
    let replies = agent.answer(|req| Message::AcquireLease { req, block });
    let refused = replies.iter().any(|m| matches!(m, Message::Failed { .. }));
    assert!(!refused, "lease still held by the hung-up pane: {replies:?}");
}

/// A block client that detached before leaving is still listed.
#[test]
fn probe_detached_block_client_lingers() {
    let path = start_server();
    let mut pane = Client::connect_with(&path, TAKEOVER, None);
    let id = pane.id();
    block_for(&mut pane);
    pane.request(|req| Message::Detach { req });
    drop(pane);
    std::thread::sleep(std::time::Duration::from_millis(500));
    let mut watcher = Client::connect_with(&path, TAKEOVER, None);
    let listed = watcher.answer(|req| Message::List { req }).into_iter().any(|m| {
        matches!(m, Message::Entry { entry: Entry::Client { client, .. }, .. } if client == id)
    });
    assert!(!listed, "detached block client still listed after its connection ended");
}
