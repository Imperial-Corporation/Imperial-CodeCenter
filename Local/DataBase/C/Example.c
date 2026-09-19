/* FUNCTION: int foo(int thing) */

void foo(int* thing){	// Initiate foo with void type and one int* argument called thing
	thing = thing + thing;		// adds to thing thing itself.
}

/*
FUNCTION DESC
foo takes one int argument and returns nothing.
foo adds to the argument to the argument itself.
To be simpler it multipiles the argument by two.
It can be used as a shortcut for "var = var + var".

FUNCTION LISENCE
..nothing..
*/