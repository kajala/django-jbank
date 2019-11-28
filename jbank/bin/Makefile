CFLAGS	+= -g $(shell xmlsec1-config --cflags) -DUNIX_SOCKETS
LDFLAGS	+= -g $(shell xmlsec1-config --libs)

all: sign3 verify3 encrypt3 decrypt3

sign3:
	gcc $(CFLAGS) sign3.c $(LDFLAGS) -o sign3

verify3:
	gcc $(CFLAGS) verify3.c $(LDFLAGS) -o verify3

encrypt3:
	gcc $(CFLAGS) encrypt3.c $(LDFLAGS) -o encrypt3

decrypt3:
	gcc $(CFLAGS) decrypt3.c $(LDFLAGS) -o decrypt3

clean:
	rm -rf sign3 verify3 encrypt3 decrypt3
