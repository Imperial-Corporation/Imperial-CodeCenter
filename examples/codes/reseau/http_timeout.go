// Client HTTP Go avec délai maximal et contexte annulable.
package main

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"time"
)

func get(url string, timeout time.Duration) (int, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	request, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return 0, err
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		return 0, err
	}
	defer response.Body.Close()
	return response.StatusCode, nil
}

func main() {
	// Serveur local volontairement lent pour provoquer le dépassement.
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(200 * time.Millisecond)
	}))
	defer server.Close()

	_, err := get(server.URL, 50*time.Millisecond)
	fmt.Println("court :", err != nil) // true : délai dépassé
	status, err := get(server.URL, time.Second)
	fmt.Println("long :", status, err) // 200 <nil>
}
