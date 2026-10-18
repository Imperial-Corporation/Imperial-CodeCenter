// Tests unitaires Rust (lancer : rustc --test lib_tests.rs && ./lib_tests).
pub fn is_palindrome(text: &str) -> bool {
    let chars: Vec<char> = text
        .chars()
        .filter(|c| c.is_alphanumeric())
        .flat_map(|c| c.to_lowercase())
        .collect();
    chars.iter().eq(chars.iter().rev())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detecte_un_palindrome() {
        assert!(is_palindrome("Esope reste ici et se repose"));
    }

    #[test]
    fn refuse_un_non_palindrome() {
        assert!(!is_palindrome("bonjour"));
    }

    #[test]
    fn chaine_vide() {
        assert!(is_palindrome(""));
    }
}
