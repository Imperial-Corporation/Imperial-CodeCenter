//! Analyse d'arguments en ligne de commande sans dépendance : options, valeurs, aide.
use std::env;
use std::process;

struct Options {
    verbose: bool,
    count: u32,
    name: String,
}

fn parse(args: &[String]) -> Result<Options, String> {
    let mut options = Options { verbose: false, count: 1, name: String::from("monde") };
    let mut iter = args.iter();

    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "-v" | "--verbose" => options.verbose = true,
            "-n" | "--count" => {
                let value = iter.next().ok_or("--count attend une valeur")?;
                options.count = value.parse().map_err(|_| format!("nombre invalide : {value}"))?;
            }
            "-h" | "--help" => return Err(String::from("Usage : cli_args [-v] [-n N] [nom]")),
            other if other.starts_with('-') => return Err(format!("option inconnue : {other}")),
            other => options.name = other.to_string(),
        }
    }

    Ok(options)
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();

    match parse(&args) {
        Ok(options) => {
            for _ in 0..options.count {
                println!("Bonjour {} !", options.name);
            }
            if options.verbose {
                eprintln!("{} salutation(s) affichée(s)", options.count);
            }
        }
        Err(message) => {
            eprintln!("{message}");
            process::exit(2);
        }
    }
}
