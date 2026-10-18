// Tests Go table-driven (lancer : go test sum_test.go).
package sum

import "testing"

func Sum(values ...int) int {
	total := 0
	for _, v := range values {
		total += v
	}
	return total
}

func TestSum(t *testing.T) {
	cases := []struct {
		name   string
		values []int
		want   int
	}{
		{"vide", nil, 0},
		{"un élément", []int{4}, 4},
		{"négatifs", []int{-1, -2, 3}, 0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := Sum(c.values...); got != c.want {
				t.Errorf("Sum(%v) = %d, attendu %d", c.values, got, c.want)
			}
		})
	}
}
