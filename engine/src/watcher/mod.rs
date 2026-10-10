use std::error::Error;
use std::path::PathBuf;
use std::sync::mpsc;
use std::time::Duration;

use notify_debouncer_full::notify::RecursiveMode;
use notify_debouncer_full::{new_debouncer, DebounceEventResult};

use tokio::sync::mpsc as tokio_mpsc;

mod events;
mod scanner;
mod worker;

type WatcherError = Box<dyn Error + Send + Sync>;

/// Démarre le scan initial et la surveillance continue.
pub async fn run(
    mut shutdown: tokio::sync::watch::Receiver<bool>,
) -> Result<(), WatcherError> {
    let configured_paths = std::env::var_os("CODECENTER_WATCH_PATHS")
        .ok_or("La variable CODECENTER_WATCH_PATHS est absente")?;

    let roots: Vec<PathBuf> = std::env::split_paths(&configured_paths)
        .filter_map(|path| match path.canonicalize() {
            Ok(path) if path.is_dir() => Some(path),
            Ok(path) => {
                eprintln!(
                    "CodeCenter : le chemin n'est pas un dossier : {}",
                    path.display()
                );
                None
            }
            Err(error) => {
                eprintln!(
                    "CodeCenter : chemin surveillé inaccessible {} : {}",
                    path.display(),
                    error
                );
                None
            }
        })
        .collect();

    if roots.is_empty() {
        return Err("Aucun dossier valide à surveiller".into());
    }

    let (event_tx, event_rx) = mpsc::channel::<DebounceEventResult>();

    // Le callback du watcher transmet les événements au worker asynchrone.
    let mut debouncer = new_debouncer(
        Duration::from_millis(500),
        None,
        move |result| {
            let _ = event_tx.send(result);
        },
    )?;

    for root in &roots {
        debouncer.watch(root, RecursiveMode::Recursive)?;
        println!(
            "CodeCenter : surveillance active sur {}",
            root.display()
        );
    }

    let (async_tx, mut async_rx) =
        tokio_mpsc::unbounded_channel::<DebounceEventResult>();

    // La réception bloquante reste sur un thread dédié.
    tokio::task::spawn_blocking(move || {
        while let Ok(result) = event_rx.recv() {
            if async_tx.send(result).is_err() {
                break;
            }
        }
    });

    // Le watcher est déjà actif : les événements survenant pendant
    // le scan initial sont conservés dans la file de réception.
    for root in &roots {
        if let Err(error) = scanner::scan_directory(root).await {
            eprintln!(
                "CodeCenter : échec du scan de {} : {}",
                root.display(),
                error
            );
        }
    }

    println!("CodeCenter : scan initial terminé, surveillance continue.");

    // Conserver le debouncer dans cette fonction maintient la surveillance.
    let _debouncer = debouncer;


    loop {
        tokio::select! {
            changed = shutdown.changed() => {
                if changed.is_err() || *shutdown.borrow_and_update() {
                    println!("CodeCenter : arrêt de la surveillance demandé.");
                    break;
                }
            }

            result = async_rx.recv() => {
                let Some(result) = result else {
                    break;
                };

                match result {
                    Ok(events) => {
                        for event in events {
                            for action in events::event_to_actions(event.event) {
                                if let Err(error) =
                                    worker::process_action(action).await
                                {
                                    eprintln!(
                                        "CodeCenter : traitement échoué : {error}"
                                    );
                                }
                            }
                        }
                    }

                    Err(errors) => {
                        for error in errors {
                            eprintln!(
                                "CodeCenter : erreur du watcher : {error}"
                            );
                        }
                    }
                }
            }
        }
    }

    println!("CodeCenter : surveillance arrêtée.");
    Ok(())
}
