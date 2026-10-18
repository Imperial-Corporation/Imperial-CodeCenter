// Tri fusion en Go : découpe récursive puis fusion de deux moitiés triées.
package main

import "fmt"

func mergeSort(values []int) []int {
	if len(values) <= 1 {
		return values
	}
	middle := len(values) / 2
	return merge(mergeSort(values[:middle]), mergeSort(values[middle:]))
}

func merge(left, right []int) []int {
	result := make([]int, 0, len(left)+len(right))
	i, j := 0, 0
	for i < len(left) && j < len(right) {
		if left[i] <= right[j] { // <= garde le tri stable
			result = append(result, left[i])
			i++
		} else {
			result = append(result, right[j])
			j++
		}
	}
	result = append(result, left[i:]...)
	return append(result, right[j:]...)
}

func main() {
	fmt.Println(mergeSort([]int{5, 2, 9, 1, 5, 6})) // [1 2 5 5 6 9]
}
