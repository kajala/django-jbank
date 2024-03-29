/** 
 * XML Security Library example: Signing a file with a dynamicaly
 * created template and an X509 certificate.
 * 
 * Signs a file using a dynamicaly created template, key from PEM file and
 * an X509 certificate. The signature has one reference with one enveloped 
 * transform to sign the whole document except the <dsig:Signature/> node 
 * itself. The key certificate is written in the <dsig:X509Data/> node.
 * 
 * This example was developed and tested with OpenSSL crypto library. The 
 * certificates management policies for another crypto library may break it.
 * 
 * Usage: 
 *      sign3 <xml-doc> <pem-key> 
 *
 * Example:
 *      ./sign3 sign3-doc.xml rsakey.pem rsacert.pem > sign3-res.xml
 *
 * The result signature could be validated using verify3 example:
 *      ./verify3 sign3-res.xml rootcert.pem
 *
 * This is free software; see Copyright file in the source
 * distribution for preciese wording.
 * 
 * Copyright (C) 2002-2003 Aleksey Sanin <aleksey@aleksey.com>
 */
#include <stdlib.h>
#include <string.h>
#include <assert.h>

#include <libxml/tree.h>
#include <libxml/xmlmemory.h>
#include <libxml/parser.h>

#ifndef XMLSEC_NO_XSLT
#include <libxslt/xslt.h>
#endif /* XMLSEC_NO_XSLT */

#include <xmlsec/xmlsec.h>
#include <xmlsec/xmltree.h>
#include <xmlsec/xmldsig.h>
#include <xmlsec/templates.h>
#include <xmlsec/crypto.h>

int sign_file(const char* xml_file, const char* key_file, const char* cert_file);

int 
main(int argc, char **argv) {
    assert(argv);

    if(argc != 4) {
        fprintf(stderr, "Error: wrong number of arguments.\n");
        fprintf(stderr, "Usage: %s <xml-file> <key-file> <cert-file>\n", argv[0]);
        return(1);
    }

    /* Init libxml and libxslt libraries */
    xmlInitParser();
    LIBXML_TEST_VERSION
    xmlLoadExtDtdDefaultValue = XML_DETECT_IDS | XML_COMPLETE_ATTRS;
    xmlSubstituteEntitiesDefault(1);
#ifndef XMLSEC_NO_XSLT
    xmlIndentTreeOutput = 1; 
#endif /* XMLSEC_NO_XSLT */
                
    /* Init xmlsec library */
    if(xmlSecInit() < 0) {
        fprintf(stderr, "Error: xmlsec initialization failed.\n");
        return(-1);
    }

    /* Check loaded library version */
    if(xmlSecCheckVersion() != 1) {
        fprintf(stderr, "Error: loaded xmlsec library version is not compatible.\n");
        return(-1);
    }

    /* Load default crypto engine if we are supporting dynamic
     * loading for xmlsec-crypto libraries. Use the crypto library
     * name ("openssl", "nss", etc.) to load corresponding 
     * xmlsec-crypto library.
     */
#ifdef XMLSEC_CRYPTO_DYNAMIC_LOADING
    if(xmlSecCryptoDLLoadLibrary(BAD_CAST XMLSEC_CRYPTO) < 0) {
        fprintf(stderr, "Error: unable to load default xmlsec-crypto library. Make sure\n"
                        "that you have it installed and check shared libraries path\n"
                        "(LD_LIBRARY_PATH) envornment variable.\n");
        return(-1);     
    }
#endif /* XMLSEC_CRYPTO_DYNAMIC_LOADING */

    /* Init crypto library */
    if(xmlSecCryptoAppInit(NULL) < 0) {
        fprintf(stderr, "Error: crypto initialization failed.\n");
        return(-1);
    }

    /* Init xmlsec-crypto library */
    if(xmlSecCryptoInit() < 0) {
        fprintf(stderr, "Error: xmlsec-crypto initialization failed.\n");
        return(-1);
    }

    if(sign_file(argv[1], argv[2], argv[3]) < 0) {
        return(-1);
    }    
    
    /* Shutdown xmlsec-crypto library */
    xmlSecCryptoShutdown();
    
    /* Shutdown crypto library */
    xmlSecCryptoAppShutdown();
    
    /* Shutdown xmlsec library */
    xmlSecShutdown();

    /* Shutdown libxslt/libxml */
#ifndef XMLSEC_NO_XSLT
    xsltCleanupGlobals();            
#endif /* XMLSEC_NO_XSLT */
    xmlCleanupParser();
    
    return(0);
}

/** 
 * sign_file:
 * @xml_file:           the XML file name.
 * @key_file:           the PEM private key file name.
 * @cert_file:          the x509 certificate PEM file.
 *
 * Signs the @xml_file using private key from @key_file and dynamicaly
 * created enveloped signature template. The certificate from @cert_file
 * is placed in the <dsig:X509Data/> node.
 *
 * Returns 0 on success or a negative value if an error occurs.
 */
int 
sign_file(const char* xml_file, const char* key_file, const char* cert_file) {
    xmlDocPtr doc = NULL;
    xmlNodePtr signNode = NULL;
    xmlNodePtr refNode = NULL;
    xmlNodePtr keyInfoNode = NULL;
    xmlNodePtr X509DataNode = NULL; //DF
    xmlNodePtr issuerSerialNode = NULL; //DF
    xmlNodePtr issuerNameNode = NULL; //DF
    xmlNodePtr issuerNumberNode = NULL; //DF
    xmlSecDSigCtxPtr dsigCtx = NULL;
    int res = -1;
    
    assert(xml_file);
    assert(key_file);
    assert(cert_file);

    /* load doc file */
    doc = xmlParseFile(xml_file);
    if ((doc == NULL) || (xmlDocGetRootElement(doc) == NULL)){
        fprintf(stderr, "Error: unable to parse file \"%s\"\n", xml_file);
        goto done;      
    }
    
#if 0
// 2010-07-26 DF: Dynamically creating this template did not work with
// Nordea server, but using existing template works!

    /* create signature template for RSA-SHA256 enveloped signature */
//    signNode = xmlSecTmplSignatureCreate(doc, xmlSecTransformExclC14NId,
//                                         xmlSecTransformRsaSha256Id, NULL);
    signNode = xmlSecTmplSignatureCreate(doc, xmlSecTransformInclC14NWithCommentsId,
                                         xmlSecTransformRsaSha256Id, NULL);
    if(signNode == NULL) {
        fprintf(stderr, "Error: failed to create signature template\n");
        goto done;              
    }

    /* add <dsig:Signature/> node to the doc */
    xmlAddChild(xmlDocGetRootElement(doc), signNode);
    
    /* add reference */
    refNode = xmlSecTmplSignatureAddReference(signNode, xmlSecTransformSha256Id,
                                        NULL, NULL, NULL);
    if(refNode == NULL) {
        fprintf(stderr, "Error: failed to add reference to signature template\n");
        goto done;              
    }

    /* add enveloped transform */
    if(xmlSecTmplReferenceAddTransform(refNode, xmlSecTransformEnvelopedId) == NULL) {
        fprintf(stderr, "Error: failed to add enveloped transform to reference\n");
        goto done;              
    }
    
    /* add <dsig:KeyInfo/> and <dsig:X509Data/> */
    keyInfoNode = xmlSecTmplSignatureEnsureKeyInfo(signNode, NULL);
    if(keyInfoNode == NULL) {
        fprintf(stderr, "Error: failed to add key info\n");
        goto done;              
    }

    X509DataNode = xmlSecTmplKeyInfoAddX509Data(keyInfoNode);
    if(X509DataNode == NULL) {
        fprintf(stderr, "Error: failed to add X509Data node\n");
        goto done;              
    }

    /* XXX: Add:
       <KeyInfo>
       <X509Data>
       <X509Certificate>...</X509Certificate>

       +<X509IssuerSerial>
       +    <X509IssuerName>serialNumber=516406-0120, CN=Nordea Corporate Server CA 01, O=Nordea Bank AB (publ), C=SE</X509IssuerName>
       +    <X509SerialNumber>11281537</X509SerialNumber>
       +</X509IssuerSerial>

       </X509Data>
       </KeyInfo>
    */
    issuerSerialNode = xmlSecTmplX509DataAddIssuerSerial(X509DataNode);
    if (issuerSerialNode == NULL) {
	fprintf(stderr, "Error: failed to add Issuer Serial\n");
	goto done;
    }
    // XXX.
    issuerNameNode = xmlSecTmplX509IssuerSerialAddIssuerName(issuerSerialNode, "2.5.4.5=#130b3531363430362d30313230,CN=Nordea role-certificates CA 01,O=Nordea Bank AB (publ),C=SE");
    if (issuerNameNode == NULL) {
	fprintf(stderr, "Error: failed to add Issuer Name\n");
	goto done;
    }
    // XXX.
    issuerNumberNode = xmlSecTmplX509IssuerSerialAddSerialNumber(issuerSerialNode, "13070788");
    if (issuerNumberNode == NULL) {
	fprintf(stderr, "Error: failed to add Serial Number\n");
	goto done;
    }
    if (xmlSecTmplX509DataAddCertificate(X509DataNode) == NULL) {
	fprintf(stderr, "Error: failed to add certificate\n");
	goto done;
    }
#else
    /* find start node */
    signNode = xmlSecFindNode(xmlDocGetRootElement(doc), xmlSecNodeSignature, xmlSecDSigNs);
    if(signNode == NULL) {
        fprintf(stderr, "Error: start node not found in \"%s\"\n", xml_file);
        goto done;      
    }
#endif

    /* create signature context, we don't need keys manager in this example */
    dsigCtx = xmlSecDSigCtxCreate(NULL);
    if(dsigCtx == NULL) {
        fprintf(stderr,"Error: failed to create signature context\n");
        goto done;
    }

    /* load private key, assuming that there is not password */
    dsigCtx->signKey = xmlSecCryptoAppKeyLoad(key_file, xmlSecKeyDataFormatPem, NULL, NULL, NULL);
    if(dsigCtx->signKey == NULL) {
        fprintf(stderr,"Error: failed to load private pem key from \"%s\"\n", key_file);
        goto done;
    }
    
    /* load certificate and add to the key */
    if(xmlSecCryptoAppKeyCertLoad(dsigCtx->signKey, cert_file, xmlSecKeyDataFormatPem) < 0) {
        fprintf(stderr,"Error: failed to load pem certificate \"%s\"\n", cert_file);
        goto done;
    }

    /* set key name to the file name, this is just an example! */
    if(xmlSecKeySetName(dsigCtx->signKey, key_file) < 0) {
        fprintf(stderr,"Error: failed to set key name for key from \"%s\"\n", key_file);
        goto done;
    }

    /* sign the template */
    if(xmlSecDSigCtxSign(dsigCtx, signNode) < 0) {
        fprintf(stderr,"Error: signature failed\n");
        goto done;
    }
        
    /* print signed document to stdout */
    xmlDocDump(stdout, doc);
    
    /* success */
    res = 0;

done:    
    /* cleanup */
    if(dsigCtx != NULL) {
        xmlSecDSigCtxDestroy(dsigCtx);
    }
    
    if(doc != NULL) {
        xmlFreeDoc(doc); 
    }
    return(res);
}

