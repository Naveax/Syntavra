#![forbid(unsafe_code)]

use std::fmt;

const INITIAL_STATE: [u32; 8] = [
    0x6a09_e667,
    0xbb67_ae85,
    0x3c6e_f372,
    0xa54f_f53a,
    0x510e_527f,
    0x9b05_688c,
    0x1f83_d9ab,
    0x5be0_cd19,
];

const ROUND_CONSTANTS: [u32; 64] = [
    0x428a_2f98,
    0x7137_4491,
    0xb5c0_fbcf,
    0xe9b5_dba5,
    0x3956_c25b,
    0x59f1_11f1,
    0x923f_82a4,
    0xab1c_5ed5,
    0xd807_aa98,
    0x1283_5b01,
    0x2431_85be,
    0x550c_7dc3,
    0x72be_5d74,
    0x80de_b1fe,
    0x9bdc_06a7,
    0xc19b_f174,
    0xe49b_69c1,
    0xefbe_4786,
    0x0fc1_9dc6,
    0x240c_a1cc,
    0x2de9_2c6f,
    0x4a74_84aa,
    0x5cb0_a9dc,
    0x76f9_88da,
    0x983e_5152,
    0xa831_c66d,
    0xb003_27c8,
    0xbf59_7fc7,
    0xc6e0_0bf3,
    0xd5a7_9147,
    0x06ca_6351,
    0x1429_2967,
    0x27b7_0a85,
    0x2e1b_2138,
    0x4d2c_6dfc,
    0x5338_0d13,
    0x650a_7354,
    0x766a_0abb,
    0x81c2_c92e,
    0x9272_2c85,
    0xa2bf_e8a1,
    0xa81a_664b,
    0xc24b_8b70,
    0xc76c_51a3,
    0xd192_e819,
    0xd699_0624,
    0xf40e_3585,
    0x106a_a070,
    0x19a4_c116,
    0x1e37_6c08,
    0x2748_774c,
    0x34b0_bcb5,
    0x391c_0cb3,
    0x4ed8_aa4a,
    0x5b9c_ca4f,
    0x682e_6ff3,
    0x748f_82ee,
    0x78a5_636f,
    0x84c8_7814,
    0x8cc7_0208,
    0x90be_fffa,
    0xa450_6ceb,
    0xbef9_a3f7,
    0xc671_78f2,
];

const OPAQUE_MANIFEST_PREFIX: &str = "benchmarks/results/real-tasks";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CanonicalPathError {
    Empty,
    Absolute,
    DrivePrefix,
    ParentTraversal,
    Nul,
}

impl CanonicalPathError {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::Empty => "PATH_EMPTY",
            Self::Absolute => "PATH_ABSOLUTE",
            Self::DrivePrefix => "PATH_DRIVE_PREFIX",
            Self::ParentTraversal => "PATH_PARENT_TRAVERSAL",
            Self::Nul => "PATH_NUL",
        }
    }
}

impl fmt::Display for CanonicalPathError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code())
    }
}

impl std::error::Error for CanonicalPathError {}

#[inline]
fn choose(x: u32, y: u32, z: u32) -> u32 {
    (x & y) ^ (!x & z)
}

#[inline]
fn majority(x: u32, y: u32, z: u32) -> u32 {
    (x & y) ^ (x & z) ^ (y & z)
}

#[inline]
fn big_sigma_zero(value: u32) -> u32 {
    value.rotate_right(2) ^ value.rotate_right(13) ^ value.rotate_right(22)
}

#[inline]
fn big_sigma_one(value: u32) -> u32 {
    value.rotate_right(6) ^ value.rotate_right(11) ^ value.rotate_right(25)
}

#[inline]
fn small_sigma_zero(value: u32) -> u32 {
    value.rotate_right(7) ^ value.rotate_right(18) ^ (value >> 3)
}

#[inline]
fn small_sigma_one(value: u32) -> u32 {
    value.rotate_right(17) ^ value.rotate_right(19) ^ (value >> 10)
}

// SHA-256 specifies the working variables as a through h. Keeping those names
// makes the round function directly auditable against the standard.
#[allow(clippy::many_single_char_names)]
#[must_use]
pub fn sha256(input: &[u8]) -> [u8; 32] {
    let bit_length = (input.len() as u64).wrapping_mul(8);
    let mut padded = Vec::with_capacity(input.len() + 72);
    padded.extend_from_slice(input);
    padded.push(0x80);
    while padded.len() % 64 != 56 {
        padded.push(0);
    }
    padded.extend_from_slice(&bit_length.to_be_bytes());

    let mut state = INITIAL_STATE;
    for chunk in padded.chunks_exact(64) {
        let mut schedule = [0_u32; 64];
        for (index, word) in chunk.chunks_exact(4).enumerate() {
            schedule[index] = u32::from_be_bytes([word[0], word[1], word[2], word[3]]);
        }
        for index in 16..64 {
            schedule[index] = small_sigma_one(schedule[index - 2])
                .wrapping_add(schedule[index - 7])
                .wrapping_add(small_sigma_zero(schedule[index - 15]))
                .wrapping_add(schedule[index - 16]);
        }

        let mut a = state[0];
        let mut b = state[1];
        let mut c = state[2];
        let mut d = state[3];
        let mut e = state[4];
        let mut f = state[5];
        let mut g = state[6];
        let mut h = state[7];

        for index in 0..64 {
            let temporary_one = h
                .wrapping_add(big_sigma_one(e))
                .wrapping_add(choose(e, f, g))
                .wrapping_add(ROUND_CONSTANTS[index])
                .wrapping_add(schedule[index]);
            let temporary_two = big_sigma_zero(a).wrapping_add(majority(a, b, c));
            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(temporary_one);
            d = c;
            c = b;
            b = a;
            a = temporary_one.wrapping_add(temporary_two);
        }

        state[0] = state[0].wrapping_add(a);
        state[1] = state[1].wrapping_add(b);
        state[2] = state[2].wrapping_add(c);
        state[3] = state[3].wrapping_add(d);
        state[4] = state[4].wrapping_add(e);
        state[5] = state[5].wrapping_add(f);
        state[6] = state[6].wrapping_add(g);
        state[7] = state[7].wrapping_add(h);
    }

    let mut output = [0_u8; 32];
    for (index, word) in state.iter().enumerate() {
        output[index * 4..index * 4 + 4].copy_from_slice(&word.to_be_bytes());
    }
    output
}

#[must_use]
pub fn bytes_to_hex(input: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(input.len() * 2);
    for &byte in input {
        output.push(char::from(HEX[usize::from(byte >> 4)]));
        output.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    output
}

#[must_use]
pub fn sha256_hex(input: &[u8]) -> String {
    bytes_to_hex(&sha256(input))
}

pub fn normalize_repository_path(input: &str) -> Result<String, CanonicalPathError> {
    if input.as_bytes().contains(&0) {
        return Err(CanonicalPathError::Nul);
    }
    let portable = input.replace('\\', "/");
    if portable.starts_with('/') {
        return Err(CanonicalPathError::Absolute);
    }
    let bytes = portable.as_bytes();
    if bytes.len() >= 2 && bytes[1] == b':' && bytes[0].is_ascii_alphabetic() {
        return Err(CanonicalPathError::DrivePrefix);
    }

    let mut parts = Vec::new();
    for part in portable.split('/') {
        match part {
            "" | "." => {}
            ".." => return Err(CanonicalPathError::ParentTraversal),
            _ => parts.push(part),
        }
    }
    if parts.is_empty() {
        return Err(CanonicalPathError::Empty);
    }
    Ok(parts.join("/"))
}

#[must_use]
pub fn canonical_text_bytes(input: &[u8]) -> Vec<u8> {
    if input.contains(&0) || std::str::from_utf8(input).is_err() {
        return input.to_vec();
    }

    let mut output = Vec::with_capacity(input.len());
    let mut index = 0;
    while index < input.len() {
        if input[index] == b'\r' {
            output.push(b'\n');
            if index + 1 < input.len() && input[index + 1] == b'\n' {
                index += 1;
            }
        } else {
            output.push(input[index]);
        }
        index += 1;
    }
    output
}

pub fn canonical_manifest_bytes(
    relative_path: &str,
    input: &[u8],
) -> Result<Vec<u8>, CanonicalPathError> {
    let normalized = normalize_repository_path(relative_path)?;
    if normalized == OPAQUE_MANIFEST_PREFIX
        || normalized.starts_with("benchmarks/results/real-tasks/")
    {
        return Ok(input.to_vec());
    }
    Ok(canonical_text_bytes(input))
}

pub fn manifest_digest_hex(
    relative_path: &str,
    input: &[u8],
) -> Result<String, CanonicalPathError> {
    Ok(sha256_hex(&canonical_manifest_bytes(relative_path, input)?))
}

#[cfg(test)]
mod tests {
    use super::{
        canonical_manifest_bytes, canonical_text_bytes, manifest_digest_hex,
        normalize_repository_path, sha256_hex, CanonicalPathError,
    };

    #[test]
    fn matches_empty_vector() {
        assert_eq!(
            sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }

    #[test]
    fn matches_abc_vector() {
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    #[test]
    fn normalizes_repository_paths_lexically() {
        assert_eq!(
            normalize_repository_path(r".\src//./nested\main.py"),
            Ok("src/nested/main.py".to_owned())
        );
        assert_eq!(
            normalize_repository_path("Müşteri/Özet.md"),
            Ok("Müşteri/Özet.md".to_owned())
        );
    }

    #[test]
    fn rejects_unsafe_repository_paths() {
        assert_eq!(
            normalize_repository_path("src/../secret"),
            Err(CanonicalPathError::ParentTraversal)
        );
        assert_eq!(
            normalize_repository_path("/etc/passwd"),
            Err(CanonicalPathError::Absolute)
        );
        assert_eq!(
            normalize_repository_path(r"C:\repo\file"),
            Err(CanonicalPathError::DrivePrefix)
        );
        assert_eq!(
            normalize_repository_path("./"),
            Err(CanonicalPathError::Empty)
        );
    }

    #[test]
    fn canonicalizes_utf8_line_endings_only() {
        assert_eq!(canonical_text_bytes(b"first\r\nsecond\r"), b"first\nsecond\n");
        let binary = b"alpha\r\n\0omega\r\n";
        assert_eq!(canonical_text_bytes(binary), binary);
        let invalid_utf8 = [0xff, b'\r', b'\n', 0xfe];
        assert_eq!(canonical_text_bytes(&invalid_utf8), invalid_utf8);
    }

    #[test]
    fn preserves_real_task_receipts_byte_for_byte() {
        let input = b"receipt\r\npayload\r\n";
        assert_eq!(
            canonical_manifest_bytes(
                "benchmarks/results/real-tasks/raw-receipt.txt",
                input
            ),
            Ok(input.to_vec())
        );
    }

    #[test]
    fn manifest_digest_matches_python_reference_vector() {
        assert_eq!(
            manifest_digest_hex(
                "syntavra_runtime/example.py",
                b"first line\r\nsecond line\r\n"
            ),
            Ok("c2097f55f01fc297fc7f4acf21438123e06e4d409a818524428534e850642f4f"
                .to_owned())
        );
    }
}
