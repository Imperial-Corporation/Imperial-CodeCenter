// Analyse d'arguments en Go avec le paquet flag.
package main

import (
	"flag"
	"fmt"
	"os"
)

func main() {
	name := flag.String("nom", "monde", "nom à saluer")
	count := flag.Int("n", 1, "nombre de répétitions")
	verbose := flag.Bool("v", false, "mode bavard")
	flag.Parse()

	if *count < 1 {
		fmt.Fprintln(os.Stderr, "n doit être >= 1")
		os.Exit(2)
	}
	if *verbose {
		fmt.Println("arguments restants :", flag.Args())
	}
	for i := 0; i < *count; i++ {
		fmt.Printf("Bonjour, %s !\n", *name)
	}
}
