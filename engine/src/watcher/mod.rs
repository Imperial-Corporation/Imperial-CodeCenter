// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

use std::error::Error;
use std::path::PathBuf;
use std::sync::mpsc;
use std::time::Duration;

use notify_debouncer_full::notify::RecursiveMode;
use notify_debouncer_full::{new_debouncer, DebounceEventResult};

use tokio::sync::mpsc as tokio_mpsc;

mod events;
mod scanner;
pub mod status;
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

    status::set_watching(true);

    // Le watcher est déjà actif : les événements survenant pendant
    // le scan initial sont conservés dans la file de réception.
    run_cycle(&roots, "initial").await;

    println!("CodeCenter : scan initial terminé, surveillance continue.");

    // Conserver le debouncer dans cette fonction maintient la surveillance.
    let _debouncer = debouncer;


    // Nouvelles tentatives des fichiers en échec (toutes les 60 s) et
    // passage de rattrapage complet optionnel (CODECENTER_RESCAN_SECS,
    // 1800 s par défaut, 0 pour désactiver). Le passage de rattrapage
    // ignore les fichiers inchangés ; il couvre les systèmes de fichiers
    // où inotify est incomplet (NFS, montages réseau, dépôt par un autre
    // utilisateur…).
    let rescan_secs: u64 = std::env::var("CODECENTER_RESCAN_SECS")
        .ok()
        .and_then(|value| value.trim().parse().ok())
        .unwrap_or(1800);
    let mut retry_timer = tokio::time::interval(Duration::from_secs(60));
    retry_timer.tick().await;
    let mut rescan_timer = tokio::time::interval(Duration::from_secs(rescan_secs.max(1)));
    rescan_timer.tick().await;

    loop {
        tokio::select! {
            _ = retry_timer.tick() => {
                for path in crate::indexing::health::retry_candidates() {
                    if !path.is_file() {
                        crate::indexing::health::record_forget(&path);
                        continue;
                    }
                    let _ = crate::indexing::document::index_file_tracked(&path).await;
                }
            }

            _ = rescan_timer.tick(), if rescan_secs > 0 => {
                run_cycle(&roots, "periodic").await;
            }

            // Passage demandé depuis l'administration (« Réindexer »).
            _ = status::rescan_notify().notified() => {
                run_cycle(&roots, "manual").await;
            }

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

    status::set_watching(false);
    println!("CodeCenter : surveillance arrêtée.");
    Ok(())
}

/// Un passage complet sur toutes les racines, avec suivi d'état.
async fn run_cycle(roots: &[PathBuf], kind: &str) {
    status::begin(kind);
    purge_excluded_if_changed().await;
    for root in roots {
        if let Err(error) = scanner::scan_directory(root).await {
            eprintln!(
                "CodeCenter : échec du passage ({kind}) sur {} : {error}",
                root.display()
            );
        }
    }
    status::finish();
}

/// Quand la liste d'exclusions change, retire de l'index les documents qui
/// y correspondent désormais (les fichiers restent sur le disque).
async fn purge_excluded_if_changed() {
    use std::sync::Mutex;
    static LAST: Mutex<Option<String>> = Mutex::new(None);

    crate::indexing::exclusions::refresh();
    let current = crate::indexing::exclusions::signature();
    {
        let mut last = LAST.lock().unwrap_or_else(|e| e.into_inner());
        let first_time = last.is_none();
        let unchanged = last.as_deref() == Some(current.as_str());
        *last = Some(current.clone());
        // Aucun motif depuis toujours : rien à retirer.
        if unchanged || (first_time && current.is_empty()) {
            return;
        }
    }

    let Ok(pool) = crate::database::get_pool().await else {
        return;
    };
    let paths: Vec<String> = match sqlx::query_scalar("SELECT storage_path FROM code_examples")
        .fetch_all(pool)
        .await
    {
        Ok(paths) => paths,
        Err(_) => return,
    };

    let mut removed = 0usize;
    for path in paths {
        if crate::indexing::exclusions::is_excluded(std::path::Path::new(&path)) {
            if let Ok(true) = crate::database::documents::remove_document_by_path(&path).await {
                // Sans cela, le fichier garderait sa signature « déjà indexé » et ne serait
                // pas ré-indexé le jour où l'exclusion est retirée.
                crate::indexing::health::record_forget(std::path::Path::new(&path));
                removed += 1;
            }
        }
    }
    if removed > 0 {
        println!("CodeCenter : {removed} document(s) retiré(s) de l'index (exclusions).");
    }
}
