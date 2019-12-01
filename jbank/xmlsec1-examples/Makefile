CFLAGS	+= -g $(shell xmlsec1-config --cflags) -DUNIX_SOCKETS
LDFLAGS	+= -g $(shell xmlsec1-config --libs)
XMLSEC1_EXAMPLES = $(HOME)/bin/xmlsec1-examples

all: sign3 verify3 encrypt3 decrypt3

sign3:
	gcc $(CFLAGS) sign3.c $(LDFLAGS) -o sign3
	mkdir -p $(XMLSEC1_EXAMPLES); mv sign3 $(XMLSEC1_EXAMPLES)

verify3:
	gcc $(CFLAGS) verify3.c $(LDFLAGS) -o verify3
	mkdir -p $(XMLSEC1_EXAMPLES); mv verify3 $(XMLSEC1_EXAMPLES)

encrypt3:
	gcc $(CFLAGS) encrypt3.c $(LDFLAGS) -o encrypt3
	mkdir -p $(XMLSEC1_EXAMPLES); mv encrypt3 $(XMLSEC1_EXAMPLES)

decrypt3:
	gcc $(CFLAGS) decrypt3.c $(LDFLAGS) -o decrypt3
	mkdir -p $(XMLSEC1_EXAMPLES); mv decrypt3 $(XMLSEC1_EXAMPLES)

clean:
	@rm -rf sign3 verify3 encrypt3 decrypt3
	@rm -rf $(XMLSEC1_EXAMPLES)
