// Signature HMAC-SHA256 d'un message et vérification en temps constant.
package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
)

func sign(key, message []byte) string {
	mac := hmac.New(sha256.New, key)
	mac.Write(message)
	return hex.EncodeToString(mac.Sum(nil))
}

func verify(key, message []byte, signature string) bool {
	expected, err := hex.DecodeString(signature)
	if err != nil {
		return false
	}
	mac := hmac.New(sha256.New, key)
	mac.Write(message)
	return hmac.Equal(mac.Sum(nil), expected) // comparaison en temps constant
}

func main() {
	key := []byte("clé-de-démonstration") // en vrai : lue depuis l'environnement
	signature := sign(key, []byte("payload"))
	fmt.Println(signature)
	fmt.Println(verify(key, []byte("payload"), signature)) // true
	fmt.Println(verify(key, []byte("modifié"), signature)) // false
}
