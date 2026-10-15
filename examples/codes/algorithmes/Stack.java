// Pile générique en Java avec tableau redimensionnable.
import java.util.Arrays;
import java.util.EmptyStackException;

public class Stack<T> {
    private Object[] items = new Object[4];
    private int size = 0;

    public void push(T item) {
        if (size == items.length) {
            items = Arrays.copyOf(items, size * 2);
        }
        items[size++] = item;
    }

    @SuppressWarnings("unchecked")
    public T pop() {
        if (size == 0) throw new EmptyStackException();
        T item = (T) items[--size];
        items[size] = null; // évite de retenir l'objet
        return item;
    }

    public boolean isEmpty() {
        return size == 0;
    }

    public static void main(String[] args) {
        Stack<String> stack = new Stack<>();
        stack.push("a");
        stack.push("b");
        System.out.println(stack.pop() + stack.pop());
    }
}
