// Arbre binaire de recherche en Java avec parcours en ordre croissant.
import java.util.ArrayList;
import java.util.List;

public class BinaryTree {
    private static class Node {
        int value;
        Node left, right;

        Node(int value) {
            this.value = value;
        }
    }

    private Node root;

    public void insert(int value) {
        root = insert(root, value);
    }

    private Node insert(Node node, int value) {
        if (node == null) return new Node(value);
        if (value < node.value) node.left = insert(node.left, value);
        else if (value > node.value) node.right = insert(node.right, value);
        return node; // les doublons sont ignorés
    }

    public List<Integer> inOrder() {
        List<Integer> result = new ArrayList<>();
        inOrder(root, result);
        return result;
    }

    private void inOrder(Node node, List<Integer> result) {
        if (node == null) return;
        inOrder(node.left, result);
        result.add(node.value);
        inOrder(node.right, result);
    }

    public static void main(String[] args) {
        BinaryTree tree = new BinaryTree();
        for (int v : new int[] {5, 3, 8, 1, 4, 8}) tree.insert(v);
        System.out.println(tree.inOrder()); // [1, 3, 4, 5, 8]
    }
}
