// Go : pool de workers avec canaux et sync.WaitGroup.
package main

import (
	"fmt"
	"sync"
)

func worker(id int, jobs <-chan int, results chan<- string, wg *sync.WaitGroup) {
	defer wg.Done()
	for job := range jobs {
		results <- fmt.Sprintf("worker %d : %d² = %d", id, job, job*job)
	}
}

func main() {
	jobs := make(chan int)
	results := make(chan string, 10)

	var wg sync.WaitGroup
	for id := 1; id <= 3; id++ {
		wg.Add(1)
		go worker(id, jobs, results, &wg)
	}

	for n := 1; n <= 6; n++ {
		jobs <- n
	}
	close(jobs)

	wg.Wait()
	close(results)

	for line := range results {
		fmt.Println(line)
	}
}
