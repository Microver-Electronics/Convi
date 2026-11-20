procedure Body;
var Doc, Board, R, P: Variant; S: String;
begin
  Doc := CreateNewDocumentFromDocumentKind('PCB');
  Board := PCBServer.GetCurrentPCBBoard;
  R := PCBServer.PCBRuleFactory(eRule_Creepage);
  R.Name := 'CreepTest';
  Board.AddPCBObject(R);
  try
    P := CreateEmptyParameterList;
    R.Export_ToParameters(P);
    L('params: ' + P.GetState_ParameterAsString);
  except
    L('export failed: ' + ExceptionMessage);
    try
      S := R.Descriptor;
      L('descriptor: ' + S);
    except
      L('no descriptor');
    end;
  end;
  Doc.SetModified(False);
  Client.CloseDocument(Doc);
end;
