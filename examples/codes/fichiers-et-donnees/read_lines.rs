//! Lecture d'un fichier ligne par ligne en Rust, avec gestion d'erreur propre.
use std::env;
use std::fs::File;
use std::io::{self, BufRead, BufReader};
use std::process;

fn count_matches(path: &str, needle: &str) -> io::Result<usize> {
    let reader = BufReader::new(File::open(path)?);
    let mut total = 0;

    for line in reader.lines() {
        if line?.contains(needle) {
            total += 1;
        }
    }

    Ok(total)
}

fn main() {
    let args: Vec<String> = env::args().collect();

    if args.len() != 3 {
        eprintln!("Usage : {} <fichier> <texte>", args[0]);
        process::exit(2);
    }

    match count_matches(&args[1], &args[2]) {
        Ok(total) => println!("{total} ligne(s) contiennent « {} »", args[2]),
        Err(error) => {
            eprintln!("Erreur : {error}");
            process::exit(1);
        }
    }
}
