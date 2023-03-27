{
    "BkToCstmrDbtCdtNtfctn": {
        "GrpHdr": {"AddtlInf": "/CRED/", "CreDtTm": "2017-02-03T08:23:10", "MsgId": "CAMT08224820170203001"},
        "Ntfctn": [
            {
                "Acct": {
                    "Ccy": "EUR",
                    "Id": {"IBAN": "FI1431313001234567"},
                    "Ownr": {"Id": {"OrgId": {"Othr": [{"Id": "5566778899", "SchmeNm": {"Cd": "BANK"}}]}}},
                    "Svcr": {"FinInstnId": {"BIC": "HANDFIHH"}},
                },
                "CreDtTm": "2017-02-03T08:23:10",
                "Id": "55667788992027122200001",
                "Ntry": [
                    {
                        "AcctSvcrRef": "201701312588CECZ0804",
                        "Amt": {"@": "88.16", "@Ccy": "EUR"},
                        "BkTxCd": {"Domn": {"Cd": "PMNT", "Fmly": {"Cd": "RCDT", "SubFmlyCd": "ESCT"}}},
                        "BookgDt": {"Dt": "2017-01-22"},
                        "CdtDbtInd": "CRDT",
                        "NtryDtls": [
                            {
                                "TxDtls": {
                                    "AmtDtls": {"InstdAmt": {"Amt": {"@": "88.16", "@Ccy": "EUR"}}, "TxAmt": {"Amt": {"@": "88.16", "@Ccy": "EUR"}}},
                                    "Refs": {"EndToEndId": "End " "To " "End " "ID " "11"},
                                    "RltdAgts": {"CdtrAgt": {"FinInstnId": {"BIC": "HANDFIHH"}}},
                                    "RltdPties": {"Dbtr": {"Nm": "SUOMI " "OY"}},
                                    "RmtInf": {"Ustrd": "9597349"},
                                }
                            }
                        ],
                        "NtryRef": "5566778899202712220000100001",
                        "Sts": "BOOK",
                        "ValDt": {"Dt": "2017-01-22"},
                    },
                    {
                        "AcctSvcrRef": "20170123456",
                        "Amt": {"@": "742.45", "@Ccy": "EUR"},
                        "BkTxCd": {"Domn": {"Cd": "PMNT", "Fmly": {"Cd": "RCDT", "SubFmlyCd": "ESCT"}}},
                        "BookgDt": {"Dt": "2017-01-22"},
                        "CdtDbtInd": "CRDT",
                        "NtryDtls": [
                            {
                                "TxDtls": {
                                    "AmtDtls": {"InstdAmt": {"Amt": {"@": "742.45", "@Ccy": "EUR"}}, "TxAmt": {"Amt": {"@": "742.45", "@Ccy": "EUR"}}},
                                    "Refs": {"EndToEndId": "End " "to " "End " "ID " "12"},
                                    "RltdAgts": {"CdtrAgt": {"FinInstnId": {"BIC": "HANDFIHH"}}},
                                    "RltdPties": {"Dbtr": {"Nm": "TEST " "OY"}},
                                    "RmtInf": {
                                        "Strd": [
                                            {
                                                "CdtrRefInf": {"Ref": "9544208", "Tp": {"CdOrPrtry": {"Cd": "SCOR"}}},
                                                "RfrdDocAmt": {"RmtdAmt": {"@": "1371.13", "@Ccy": "EUR"}},
                                            },
                                            {
                                                "RfrdDocAmt": {"CdtNoteAmt": {"@": "628.68", "@Ccy": "EUR"}},
                                                "RfrdDocInf": [{"Nb": "9582095", "Tp": {"CdOrPrtry": {"Cd": "CREN"}}}],
                                            },
                                        ]
                                    },
                                }
                            }
                        ],
                        "NtryRef": "5566778899202712220000100002",
                        "Sts": "BOOK",
                        "ValDt": {"Dt": "2017-01-22"},
                    },
                    {
                        "AcctSvcrRef": "201702013131LG123456",
                        "Amt": {"@": "6000.54", "@Ccy": "EUR"},
                        "BkTxCd": {"Domn": {"Cd": "PMNT", "Fmly": {"Cd": "RCDT", "SubFmlyCd": "ESCT"}}},
                        "BookgDt": {"Dt": "2017-01-22"},
                        "CdtDbtInd": "CRDT",
                        "NtryDtls": [
                            {
                                "TxDtls": {
                                    "AmtDtls": {"InstdAmt": {"Amt": {"@": "6000.54", "@Ccy": "EUR"}}, "TxAmt": {"Amt": {"@": "6000.54", "@Ccy": "EUR"}}},
                                    "Refs": {"EndToEndId": "EndToEndId " "13"},
                                    "RltdAgts": {"CdtrAgt": {"FinInstnId": {"BIC": "HANDFIHH"}}},
                                    "RltdPties": {"Dbtr": {"Nm": "DEBTOR " "FINLAND " "OY", "PstlAdr": {"StrtNm": "Street " "ABC"}}},
                                    "RmtInf": {
                                        "Strd": [
                                            {
                                                "RfrdDocAmt": {"RmtdAmt": {"@": "6256.7", "@Ccy": "EUR"}},
                                                "RfrdDocInf": [{"Nb": "9580572", "Tp": {"CdOrPrtry": {"Cd": "CINV"}}}],
                                            },
                                            {
                                                "RfrdDocAmt": {"CdtNoteAmt": {"@": "166.46", "@Ccy": "EUR"}},
                                                "RfrdDocInf": [{"Nb": "00000000000009580521", "Tp": {"CdOrPrtry": {"Cd": "CREN"}}}],
                                            },
                                            {
                                                "RfrdDocAmt": {"CdtNoteAmt": {"@": "89.7", "@Ccy": "EUR"}},
                                                "RfrdDocInf": [{"Nb": "00000000000009579095", "Tp": {"CdOrPrtry": {"Cd": "CREN"}}}],
                                            },
                                        ]
                                    },
                                }
                            }
                        ],
                        "NtryRef": "5566778899202712220000100003",
                        "Sts": "BOOK",
                        "ValDt": {"Dt": "2017-01-22"},
                    },
                    {
                        "AcctSvcrRef": "170130313190U60111",
                        "Amt": {"@": "216.85", "@Ccy": "EUR"},
                        "BkTxCd": {"Domn": {"Cd": "PMNT", "Fmly": {"Cd": "RCDT", "SubFmlyCd": "XBCT"}}},
                        "BookgDt": {"Dt": "2017-01-22"},
                        "CdtDbtInd": "CRDT",
                        "NtryDtls": [
                            {
                                "TxDtls": {
                                    "AmtDtls": {"InstdAmt": {"Amt": {"@": "2082.58", "@Ccy": "SEK"}}, "TxAmt": {"Amt": {"@": "216.85", "@Ccy": "EUR"}}},
                                    "RltdAgts": {"CdtrAgt": {"FinInstnId": {"BIC": "HANDFIHH"}}},
                                    "RltdPties": {"Dbtr": {"Nm": "Debtor " "Name"}},
                                    "RmtInf": {
                                        "Ustrd": [
                                            "3131090U20130000                   " "PANO/INSATTN  " "EUR            " "216,85",
                                            "KURSSI/KURS        " "9,60400MAKSU/UPPDR.  " "SEK           " "2082,58",
                                            "ULK.ARVOPV/UTL.VALUT.DAG " "01.02.2017MAKSUMR./BET. " "ORDER",
                                            "PARTIAL " "PYMT " "170124         " "INV " "43459",
                                            "INV " "EUR " "357",
                                        ]
                                    },
                                }
                            }
                        ],
                        "NtryRef": "5566778899202712220000100004",
                        "Sts": "BOOK",
                        "ValDt": {"Dt": "2017-01-22"},
                    },
                ],
                "TxsSummry": {"TtlNtries": {"CdtDbtInd": "CRDT", "NbOfNtries": 4, "Sum": "7048"}},
            }
        ],
    }
}
