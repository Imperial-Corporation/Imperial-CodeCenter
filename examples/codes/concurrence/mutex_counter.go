// Compteur partagé protégé par un sync.Mutex et attendu via WaitGroup.
package main

import (
	"fmt"
	"sync"
)

type Counter struct {
	mu    sync.Mutex
	value int
}

func (c *Counter) Inc() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.value++
}

func main() {
	var counter Counter
	var wg sync.WaitGroup

	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 100; j++ {
				counter.Inc()
			}
		}()
	}
	wg.Wait()
	fmt.Println(counter.value) // 10000
}
