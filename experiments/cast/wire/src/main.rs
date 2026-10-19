//! No speaker client, application impersonation, media fetch or credential import.
// Pinned upstream modules are supplied by the hash-checked Docker build.
#[allow(dead_code)]
#[path = "../vendor/src/crypto.rs"]
mod crypto;
#[allow(dead_code)]
#[path = "../vendor/src/proto.rs"]
mod proto;

use anyhow::{Result, ensure};
use crypto::Identity;
use prost::Message;
use proto::cast_channel::*;
use serde_json::{Value, json};
use std::{collections::BTreeMap, net::Ipv4Addr, path::PathBuf, sync::Arc, time::Duration};
use tokio::{io::{AsyncReadExt, AsyncWriteExt}, net::{TcpListener, TcpStream}, sync::{Mutex, Semaphore}};
use tokio_rustls::TlsAcceptor;

const AUTH: &str = "urn:x-cast:com.google.cast.tp.deviceauth";
const RECEIVER: &str = "urn:x-cast:com.google.cast.receiver";
const MEDIA: &str = "urn:x-cast:com.google.cast.media";
const HEARTBEAT: &str = "urn:x-cast:com.google.cast.tp.heartbeat";
const CONNECTION: &str = "urn:x-cast:com.google.cast.tp.connection";
const MAX_FRAME: usize = 65536;

struct State {
    counters: Mutex<BTreeMap<&'static str, u64>>,
    path: PathBuf,
}
impl State {
    async fn count(&self, event: &'static str) -> Result<()> {
        let mut counts = self.counters.lock().await;
        let count = counts.entry(event).or_default();
        *count = count.saturating_add(1);
        let status = json!({"mode":"diagnostic_only", "senderAcceptance":"unverified",
            "deviceCertificate":"self_signed_not_google_trusted", "mediaEnabled":false,
            "counters":*counts});
        let tmp = self.path.with_extension("pending");
        tokio::fs::write(&tmp, serde_json::to_vec(&status)?).await?;
        tokio::fs::rename(tmp, &self.path).await?;
        Ok(())
    }
}

fn reply(msg: &CastMessage, payload: Value) -> CastMessage {
    CastMessage { protocol_version: 0, source_id: msg.destination_id.clone(),
        destination_id: msg.source_id.clone(), namespace: msg.namespace.clone(),
        payload_type: PayloadType::String as i32, payload_utf8: Some(payload.to_string()),
        ..Default::default() }
}

fn json_response(msg: &CastMessage) -> Result<Option<CastMessage>> {
    let payload: Value = serde_json::from_str(msg.payload_utf8.as_deref().unwrap_or("{}"))?;
    let kind = payload["type"].as_str().unwrap_or("");
    let rid = payload["requestId"].as_u64().unwrap_or(0);
    let response = match (msg.namespace.as_str(), kind) {
        (HEARTBEAT, "PING") => json!({"type":"PONG"}),
        (CONNECTION, _) | (HEARTBEAT, "PONG") => return Ok(None),
        (RECEIVER, "GET_STATUS") => json!({"type":"RECEIVER_STATUS", "requestId":rid,
            "status":{"applications":[], "isActiveInput":false, "isStandBy":true}}),
        (RECEIVER, "GET_APP_AVAILABILITY") => {
            let mut availability = serde_json::Map::new();
            if let Some(ids) = payload["appId"].as_array().or(payload["appIds"].as_array()) {
                for id in ids.iter().take(32).filter_map(Value::as_str) {
                    availability.insert(id.to_owned(), json!("APP_UNAVAILABLE"));
                }
            } else if let Some(id) = payload["appId"].as_str() {
                availability.insert(id.to_owned(), json!("APP_UNAVAILABLE"));
            }
            json!({"type":"RECEIVER_STATUS", "requestId":rid, "availability":availability})
        }
        (RECEIVER, "LAUNCH") => json!({"type":"LAUNCH_ERROR", "requestId":rid,
            "reason":"NOT_FOUND"}),
        (MEDIA, _) => json!({"type":"INVALID_REQUEST", "requestId":rid,
            "reason":"INVALID_MEDIA_SESSION_ID"}),
        _ => json!({"type":"INVALID_REQUEST", "requestId":rid, "reason":"INVALID_COMMAND"}),
    };
    Ok(Some(reply(msg, response)))
}

fn auth_response(msg: &CastMessage, identity: &Identity) -> Result<CastMessage> {
    let message = DeviceAuthMessage::decode(msg.payload_binary.as_deref().unwrap_or_default())?;
    let challenge = message.challenge.ok_or_else(|| anyhow::anyhow!("challenge_required"))?;
    ensure!(challenge.hash_algorithm == Some(HashAlgorithm::Sha256 as i32), "sha256_required");
    ensure!(challenge.signature_algorithm.unwrap_or(SignatureAlgorithm::RsassaPkcs1v15 as i32)
        == SignatureAlgorithm::RsassaPkcs1v15 as i32, "unsupported_signature");
    let nonce = challenge.sender_nonce.unwrap_or_default();
    ensure!(!nonce.is_empty() && nonce.len() <= 1024, "invalid_nonce");
    let mut input = nonce.clone();
    input.extend_from_slice(identity.tls_cert_der());
    let auth = DeviceAuthMessage { response: Some(AuthResponse {
        signature: identity.sign(&input), client_auth_certificate: identity.auth_cert_der().to_vec(),
        intermediate_certificate: vec![], signature_algorithm: Some(SignatureAlgorithm::RsassaPkcs1v15 as i32),
        sender_nonce: Some(nonce), hash_algorithm: Some(HashAlgorithm::Sha256 as i32), crl: None,
    }), ..Default::default() };
    Ok(CastMessage { payload_type: PayloadType::Binary as i32, payload_utf8: None,
        payload_binary: Some(auth.encode_to_vec()), ..reply(msg, Value::Null) })
}

async fn connection(tcp: TcpStream, acceptor: TlsAcceptor, identity: Arc<Identity>, state: Arc<State>) -> Result<()> {
    state.count("tcpAccepted").await?;
    let mut stream = tokio::time::timeout(Duration::from_secs(10), acceptor.accept(tcp)).await??;
    state.count("tlsEstablished").await?;
    loop {
        let size = tokio::time::timeout(Duration::from_secs(60), stream.read_u32()).await?? as usize;
        ensure!(size > 0 && size <= MAX_FRAME, "frame_size");
        let mut bytes = vec![0; size];
        tokio::time::timeout(Duration::from_secs(10), stream.read_exact(&mut bytes)).await??;
        let msg = CastMessage::decode(bytes.as_slice())?;
        ensure!(msg.source_id.len() <= 256 && msg.destination_id.len() <= 256, "id_size");
        let response = if msg.namespace == AUTH {
            state.count("authChallengeReceived").await?;
            Some(auth_response(&msg, &identity)?)
        } else {
            if msg.namespace == RECEIVER {
                state.count("receiverMessageReceived").await?;
                let payload: Value = serde_json::from_str(msg.payload_utf8.as_deref().unwrap_or("{}"))?;
                if payload["type"] == "LAUNCH" { state.count("applicationLaunchRejected").await?; }
            }
            if msg.namespace == MEDIA { state.count("mediaRejected").await?; }
            json_response(&msg)?
        };
        if let Some(response) = response {
            let data = response.encode_to_vec();
            stream.write_u32(data.len() as u32).await?;
            stream.write_all(&data).await?;
            if msg.namespace == AUTH { state.count("authResponseSentNotAccepted").await?; }
        }
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    if std::env::var("CAST_EXPERIMENT").as_deref() != Ok("1") {
        println!("cast_experiment_disabled");
        return Ok(());
    }
    let ip: Ipv4Addr = std::env::var("CAST_BIND_ADDRESS")?.parse()?;
    ensure!(!ip.is_unspecified() && !ip.is_multicast(), "explicit_ipv4_required");
    let port: u16 = std::env::var("CAST_PORT").unwrap_or("8809".into()).parse()?;
    ensure!(port >= 1024, "invalid_port");
    let data = PathBuf::from(std::env::var("CAST_DATA_DIR").unwrap_or("/cast-data".into()));
    tokio::fs::create_dir_all(&data).await?;
    let state = Arc::new(State { counters: Mutex::new(BTreeMap::new()), path: data.join("wire-status.json") });
    // No --cert/--key option; no imported or extracted hardware identity.
    let identity = Arc::new(Identity::generate()?);
    // Public certificate only, to pin strict TLS verification in offline fixtures.
    // No private key is persisted and no Google trust/SDK acceptance is implied.
    tokio::fs::write(data.join("diagnostic-tls.der"), identity.tls_cert_der()).await?;
    let config = rustls::ServerConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
        .with_safe_default_protocol_versions()?.with_no_client_auth()
        .with_single_cert(vec![rustls::pki_types::CertificateDer::from(identity.tls_cert_der().to_vec())],
            identity.tls_private_key_der()?)?;
    let acceptor = TlsAcceptor::from(Arc::new(config));
    let listener = TcpListener::bind((ip, port)).await?;
    state.count("listenerReady").await?;
    println!("cast_diagnostic_ready_media_blocked");
    let slots = Arc::new(Semaphore::new(8));
    loop {
        let (tcp, _) = listener.accept().await?;
        let Ok(permit) = slots.clone().try_acquire_owned() else { continue; };
        let (acceptor, identity, state) = (acceptor.clone(), identity.clone(), state.clone());
        tokio::spawn(async move {
            let _permit = permit;
            if connection(tcp, acceptor, identity, state.clone()).await.is_err() {
                let _ = state.count("connectionEndedOrRejected").await;
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn app_and_media_never_claim_success() {
        for (ns, payload, expected) in [
            (RECEIVER, json!({"type":"LAUNCH","requestId":4,"appId":"FD1F76C5"}), "LAUNCH_ERROR"),
            (MEDIA, json!({"type":"LOAD","requestId":5,"currentTime":7200}), "INVALID_REQUEST"),
        ] {
            let msg = CastMessage { namespace: ns.into(), payload_utf8: Some(payload.to_string()), ..Default::default() };
            let response = json_response(&msg).unwrap().unwrap();
            let value: Value = serde_json::from_str(response.payload_utf8.as_ref().unwrap()).unwrap();
            assert_eq!(value["type"], expected);
            assert_eq!(value["requestId"], payload["requestId"]);
        }
    }
    #[test]
    fn all_apps_are_unavailable() {
        let msg = CastMessage { namespace: RECEIVER.into(), payload_utf8: Some(json!({
            "type":"GET_APP_AVAILABILITY","appId":["FD1F76C5","CC1AD845"]}).to_string()), ..Default::default() };
        let response = json_response(&msg).unwrap().unwrap();
        assert!(!response.payload_utf8.unwrap().contains("APP_AVAILABLE"));
    }
    #[test]
    fn auth_challenge_is_bound_but_not_trusted() {
        let identity = Identity::generate().unwrap();
        let auth = DeviceAuthMessage { challenge: Some(AuthChallenge { sender_nonce: Some(vec![7;16]),
            hash_algorithm: Some(HashAlgorithm::Sha256 as i32), signature_algorithm: Some(1) }), ..Default::default() };
        let msg = CastMessage { namespace: AUTH.into(), payload_binary: Some(auth.encode_to_vec()), ..Default::default() };
        let response = auth_response(&msg, &identity).unwrap();
        let decoded = DeviceAuthMessage::decode(response.payload_binary.unwrap().as_slice()).unwrap().response.unwrap();
        assert_eq!(decoded.sender_nonce.unwrap(), vec![7;16]);
        assert_eq!(decoded.client_auth_certificate, identity.auth_cert_der());
        assert!(decoded.intermediate_certificate.is_empty());
        assert!(!decoded.signature.is_empty());
        assert!(auth_response(&CastMessage::default(), &identity).is_err());
    }
}
