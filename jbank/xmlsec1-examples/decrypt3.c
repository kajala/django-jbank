/** 
 * XML Security Library example: Decrypting an encrypted file using a custom keys manager.
 * 
 * Decrypts encrypted XML file using a custom files based keys manager.
 * We assume that key's name in <dsig:KeyName/> element is just 
 * key's file name in the current folder.
 * 
 * Usage: 
 *      ./decrypt3 <xml-enc> 
 *
 * Example:
 *      ./decrypt3 encrypt1-res.xml
 *      ./decrypt3 encrypt2-res.xml
 *
 * This is free software; see Copyright file in the source
 * distribution for preciese wording.
 * 
 * Copyright (C) 2002-2003 Aleksey Sanin <aleksey@aleksey.com>
 */
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <assert.h>

#include <libxml/tree.h>
#include <libxml/xmlmemory.h>
#include <libxml/parser.h>

#ifndef XMLSEC_NO_XSLT
#include <libxslt/xslt.h>
#endif /* XMLSEC_NO_XSLT */

#include <xmlsec/xmlsec.h>
#include <xmlsec/xmltree.h>
#include <xmlsec/xmlenc.h>
#include <xmlsec/crypto.h>

//xmlSecKeyStoreId  files_keys_store_get_klass(void);
xmlSecKeysMngrPtr create_keys_mngr(char* key_file);
int decrypt_file(xmlSecKeysMngrPtr mngr, const char* enc_file);


// ---- MAIN -----
int main(int argc, char **argv) {
    xmlSecKeysMngrPtr mngr;

    assert(argv);

    if(argc != 3) {
        fprintf(stderr, "Error: wrong number of arguments.\n");
        fprintf(stderr, "Usage: %s <enc-file> <key-file>\n", argv[0]);
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

    /* create keys manager and load keys */
    mngr = create_keys_mngr(argv[2]);
    if(mngr == NULL) {
        return(-1);
    }

    if(decrypt_file(mngr, argv[1]) < 0) {
        xmlSecKeysMngrDestroy(mngr);    
        return(-1);
    }    

    /* destroy keys manager */
    xmlSecKeysMngrDestroy(mngr);
    
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
 * decrypt_file:
 * @mngr:               the pointer to keys manager.
 * @enc_file:           the encrypted XML  file name.
 *
 * Decrypts the XML file #enc_file using DES key from #key_file and 
 * prints results to stdout.
 *
 * Returns 0 on success or a negative value if an error occurs.
 */
int 
decrypt_file(xmlSecKeysMngrPtr mngr, const char* enc_file) {
    xmlDocPtr doc = NULL;
    xmlNodePtr node = NULL;
    xmlSecEncCtxPtr encCtx = NULL;
    int res = -1;
    
    assert(mngr);
    assert(enc_file);

    /* load template */
    doc = xmlParseFile(enc_file);
    if ((doc == NULL) || (xmlDocGetRootElement(doc) == NULL)){
        fprintf(stderr, "Error: unable to parse file \"%s\"\n", enc_file);
        goto done;      
    }
    
    /* find start node */
    node = xmlSecFindNode(xmlDocGetRootElement(doc), xmlSecNodeEncryptedData, xmlSecEncNs);
    if(node == NULL) {
        fprintf(stderr, "Error: start node not found in \"%s\"\n", enc_file);
        goto done;      
    }

    /* create encryption context */
    encCtx = xmlSecEncCtxCreate(mngr);
    if(encCtx == NULL) {
        fprintf(stderr,"Error: failed to create encryption context\n");
        goto done;
    }


    /* decrypt the data */
    if((xmlSecEncCtxDecrypt(encCtx, node) < 0) || (encCtx->result == NULL)) {
        fprintf(stderr,"Error: decryption failed\n");
        goto done;
    }
        
    /* print decrypted data to stdout */
    if(encCtx->resultReplaced != 0) {
	//fprintf(stdout, "Decrypted XML data:\n");
        xmlDocDump(stdout, doc);
    } else {
      //fprintf(stdout, "Decrypted binary data (%d bytes):\n", xmlSecBufferGetSize(encCtx->result));
        if(xmlSecBufferGetData(encCtx->result) != NULL) {
            fwrite(xmlSecBufferGetData(encCtx->result), 
                  1, 
                  xmlSecBufferGetSize(encCtx->result),
                  stdout);
        }
    }
    //fprintf(stdout, "\n");
        
    /* success */
    res = 0;

done:    
    /* cleanup */
    if(encCtx != NULL) {
        //xmlSecEncCtxDestroy(encCtx);
    }
    
    if(doc != NULL) {
        xmlFreeDoc(doc); 
    }
    return(res);
}

/**
 * create_keys_mngr:
 *  
 * Creates a files based keys manager: we assume that key name is 
 * the key file name,
 *
 * Returns pointer to newly created keys manager or NULL if an error occurs.
 */
xmlSecKeysMngrPtr 
create_keys_mngr(char* key_file) {
    xmlSecKeysMngrPtr mngr;
    xmlSecKeyPtr key;

    // create keys manager
    mngr = xmlSecKeysMngrCreate();
    if (mngr == NULL) {
        fprintf(stderr, "Error: failed to create keys manager.\n");
        return(NULL);
    }

    // initialize it
    if (xmlSecCryptoAppDefaultKeysMngrInit(mngr) < 0) {
        fprintf(stderr, "Error: failed to initialize keys manager.\n");
        xmlSecKeysMngrDestroy(mngr);
        return(NULL);
    }    

    // load RSA key
    key = xmlSecCryptoAppKeyLoad(key_file, xmlSecKeyDataFormatPem, NULL, NULL, NULL);
    if (key == NULL) {
        fprintf(stderr,"Error: failed to load rsa key from file \"%s\"\n", key_file);
        xmlSecKeysMngrDestroy(mngr);
        return(NULL);
    }

    // add key to keys manager
    if (xmlSecCryptoAppDefaultKeysMngrAdoptKey(mngr, key) < 0) {
        fprintf(stderr,"Error: failed to add key from \"%s\" to keys manager\n", key_file);
        xmlSecKeyDestroy(key);
        xmlSecKeysMngrDestroy(mngr);
	return(NULL);
    }

    return(mngr);
}
