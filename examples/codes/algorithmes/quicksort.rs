//! Tri rapide générique en Rust, en place, avec partition de Lomuto.

fn partition<T: PartialOrd>(items: &mut [T]) -> usize {
    let pivot = items.len() - 1;
    let mut store = 0;

    for i in 0..pivot {
        if items[i] <= items[pivot] {
            items.swap(i, store);
            store += 1;
        }
    }

    items.swap(store, pivot);
    store
}

pub fn quicksort<T: PartialOrd>(items: &mut [T]) {
    if items.len() <= 1 {
        return;
    }

    let pivot = partition(items);
    let (left, right) = items.split_at_mut(pivot);
    quicksort(left);
    quicksort(&mut right[1..]);
}

fn main() {
    let mut numbers = vec![9, 4, 7, 1, 8, 2];
    quicksort(&mut numbers);
    println!("{numbers:?}");
}
