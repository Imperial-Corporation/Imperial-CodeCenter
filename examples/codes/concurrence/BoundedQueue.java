// File bornée producteur/consommateur avec wait/notify.
import java.util.ArrayDeque;
import java.util.Queue;

public class BoundedQueue<T> {
    private final Queue<T> items = new ArrayDeque<>();
    private final int capacity;

    public BoundedQueue(int capacity) {
        this.capacity = capacity;
    }

    public synchronized void put(T item) throws InterruptedException {
        while (items.size() == capacity) wait(); // file pleine
        items.add(item);
        notifyAll();
    }

    public synchronized T take() throws InterruptedException {
        while (items.isEmpty()) wait(); // file vide
        T item = items.remove();
        notifyAll();
        return item;
    }

    public static void main(String[] args) throws InterruptedException {
        BoundedQueue<Integer> queue = new BoundedQueue<>(2);
        Thread producer = new Thread(() -> {
            try {
                for (int i = 1; i <= 5; i++) queue.put(i);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        });
        producer.start();
        int sum = 0;
        for (int i = 0; i < 5; i++) sum += queue.take();
        producer.join();
        System.out.println("somme = " + sum); // 15
    }
}
