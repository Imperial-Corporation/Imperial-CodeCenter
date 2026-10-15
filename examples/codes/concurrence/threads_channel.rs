//! Rust : threads et canal mpsc, puis agrégation des résultats.
use std::sync::mpsc;
use std::thread;

fn main() {
    let (sender, receiver) = mpsc::channel();

    let handles: Vec<_> = (1..=4)
        .map(|id| {
            let sender = sender.clone();
            thread::spawn(move || {
                let somme: u64 = (1..=id * 1000).sum();
                sender.send((id, somme)).expect("le récepteur est fermé");
            })
        })
        .collect();

    drop(sender); // sans cela, la boucle ci-dessous ne se terminerait jamais

    let mut results: Vec<(u64, u64)> = receiver.iter().collect();
    results.sort();

    for handle in handles {
        handle.join().expect("un thread a paniqué");
    }

    for (id, somme) in results {
        println!("thread {id} : somme = {somme}");
    }
}
