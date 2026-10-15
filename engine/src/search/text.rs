//! Fonctions pures de recherche textuelle : découpage de la requête,
//! scoring et extraction d'extraits. Aucune dépendance base de données
//! ni disque, ce qui les rend entièrement testables.

/// Nombre maximal de termes conservés dans une requête.
pub const MAX_TERMS: usize = 12;

/// Taille de la fenêtre d'extrait, en lignes.
pub const SNIPPET_LINES: usize = 14;

/// Taille maximale d'un extrait, en caractères.
pub const SNIPPET_MAX_CHARS: usize = 1500;

/// Mots vides français et anglais ignorés dans les requêtes.
const STOPWORDS: &[&str] = &[
    "le", "la", "les", "un", "une", "des", "du", "de", "et", "ou", "en",
    "au", "aux", "ce", "ces", "pour", "par", "sur", "dans", "avec", "que",
    "qui", "est", "comment", "the", "an", "of", "and", "or", "to", "in",
    "is", "for", "on", "with", "how", "what", "do", "does", "from", "it",
];

/// Découpe une requête en termes en minuscules, sans doublon ni mot vide.
///
/// Les caractères alphanumériques et le tiret bas sont conservés : un
/// identifiant comme `parse_json` reste un seul terme.
pub fn tokenize(query: &str) -> Vec<String> {
    let mut terms: Vec<String> = Vec::new();

    for raw in query.split(|c: char| !(c.is_alphanumeric() || c == '_')) {
        let term = raw.to_lowercase();

        if term.chars().count() < 2 || STOPWORDS.contains(&term.as_str()) {
            continue;
        }

        if !terms.contains(&term) {
            terms.push(term);
        }

        if terms.len() >= MAX_TERMS {
            break;
        }
    }

    terms
}

/// Compte les occurrences (sans chevauchement) d'un terme dans un texte
/// déjà passé en minuscules, en s'arrêtant à `cap`.
pub fn count_occurrences(haystack: &str, term: &str, cap: usize) -> usize {
    if term.is_empty() {
        return 0;
    }

    haystack
        .match_indices(term)
        .take(cap)
        .count()
}

/// Score fondé sur les métadonnées (titre, chemin, catégories, résumé).
pub fn metadata_score(
    terms: &[String],
    title: &str,
    path: &str,
    categories: &[String],
    language: &str,
    summary: &str,
) -> f64 {
    let title = title.to_lowercase();
    let path = path.to_lowercase();
    let categories = categories.join(" ").to_lowercase();
    let language = language.to_lowercase();
    let summary = summary.to_lowercase();

    let mut score = 0.0;

    for term in terms {
        if title.contains(term.as_str()) {
            score += 3.0;
        }
        if path.contains(term.as_str()) || categories.contains(term.as_str()) {
            score += 2.0;
        }
        if language == *term {
            score += 1.0;
        }
        if summary.contains(term.as_str()) {
            score += 1.5;
        }
    }

    score
}

/// Score fondé sur le contenu. `lowered` est le contenu en minuscules.
pub fn content_score(terms: &[String], query: &str, lowered: &str) -> f64 {
    if terms.is_empty() {
        return 0.0;
    }

    let mut score = 0.0;
    let mut matched = 0usize;

    for term in terms {
        let count = count_occurrences(lowered, term, 20);

        if count > 0 {
            matched += 1;
            // Le logarithme évite qu'un terme répété écrase les autres.
            score += (1.0 + count as f64).ln();
        }
    }

    // Favorise les fichiers qui couvrent tous les termes de la requête.
    score += 4.0 * matched as f64 / terms.len() as f64;

    let phrase = query.trim().to_lowercase();

    if phrase.chars().count() >= 4 && lowered.contains(&phrase) {
        score += 3.0;
    }

    score
}

/// Extrait de code avec ses numéros de ligne (1-indexés, inclusifs).
#[derive(Debug, Clone, PartialEq)]
pub struct Snippet {
    pub line_start: usize,
    pub line_end: usize,
    pub text: String,
}

/// Choisit la fenêtre de lignes contenant le plus de termes distincts.
///
/// Sans aucune correspondance, retourne le début du fichier.
pub fn best_snippet(content: &str, terms: &[String]) -> Snippet {
    let lines: Vec<&str> = content.lines().collect();

    if lines.is_empty() {
        return Snippet {
            line_start: 1,
            line_end: 1,
            text: String::new(),
        };
    }

    let per_line: Vec<usize> = lines
        .iter()
        .map(|line| {
            let lowered = line.to_lowercase();
            terms
                .iter()
                .filter(|term| lowered.contains(term.as_str()))
                .count()
        })
        .collect();

    let window = SNIPPET_LINES.min(lines.len());
    let mut current: usize = per_line[..window].iter().sum();
    let mut best = current;
    let mut best_start = 0usize;

    for start in 1..=(lines.len() - window) {
        current += per_line[start + window - 1];
        current -= per_line[start - 1];

        if current > best {
            best = current;
            best_start = start;
        }
    }

    // Recentre légèrement la fenêtre pour montrer le contexte avant.
    if best > 0 {
        let first_match = (best_start..best_start + window)
            .find(|index| per_line[*index] > 0)
            .unwrap_or(best_start);
        best_start = first_match.saturating_sub(2).min(lines.len() - window);
    }

    let end = best_start + window;
    let mut text = lines[best_start..end].join("\n");

    if text.chars().count() > SNIPPET_MAX_CHARS {
        text = text.chars().take(SNIPPET_MAX_CHARS).collect();
    }

    Snippet {
        line_start: best_start + 1,
        line_end: end,
        text,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokenize_supprime_mots_vides_et_doublons() {
        let terms = tokenize("Comment parser le JSON avec parse_json json ?");
        assert_eq!(terms, vec!["parser", "json", "parse_json"]);
    }

    #[test]
    fn tokenize_limite_le_nombre_de_termes() {
        let query = (0..40).map(|i| format!("mot{i}")).collect::<Vec<_>>().join(" ");
        assert_eq!(tokenize(&query).len(), MAX_TERMS);
    }

    #[test]
    fn tokenize_requete_vide() {
        assert!(tokenize("  ?! ").is_empty());
    }

    #[test]
    fn score_metadonnees_pondere_titre() {
        let terms = vec!["json".to_string()];
        let titre = metadata_score(&terms, "json.rs", "/a/b", &[], "Rust", "");
        let chemin = metadata_score(&terms, "x.rs", "/json/b", &[], "Rust", "");
        assert!(titre > chemin);
    }

    #[test]
    fn score_contenu_favorise_la_couverture() {
        let terms = vec!["alpha".to_string(), "beta".to_string()];
        let deux = content_score(&terms, "alpha beta", "alpha beta");
        let un = content_score(&terms, "alpha beta", "alpha alpha alpha");
        assert!(deux > un);
    }

    #[test]
    fn score_contenu_nul_sans_correspondance() {
        let terms = vec!["zzz".to_string()];
        assert_eq!(content_score(&terms, "zzz", "rien ici"), 0.0);
    }

    #[test]
    fn snippet_centre_sur_la_correspondance() {
        let mut content = String::new();
        for i in 1..=100 {
            if i == 60 {
                content.push_str("fn cible() {}\n");
            } else {
                content.push_str(&format!("ligne {i}\n"));
            }
        }
        let terms = vec!["cible".to_string()];
        let snippet = best_snippet(&content, &terms);
        assert!(snippet.line_start <= 60 && snippet.line_end >= 60);
        assert!(snippet.text.contains("fn cible"));
        assert_eq!(snippet.line_end - snippet.line_start + 1, SNIPPET_LINES);
    }

    #[test]
    fn snippet_sans_correspondance_retourne_le_debut() {
        let snippet = best_snippet("a\nb\nc", &["zz".to_string()]);
        assert_eq!((snippet.line_start, snippet.line_end), (1, 3));
    }

    #[test]
    fn snippet_fichier_vide() {
        let snippet = best_snippet("", &["a".to_string()]);
        assert!(snippet.text.is_empty());
    }
}
