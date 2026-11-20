procedure TryP(R: Variant; N: String);
begin
  try
    if N = 'MinCreepage' then R.MinCreepage := MMsToCoord(2.5)
    else if N = 'Creepage' then R.Creepage := MMsToCoord(2.5)
    else if N = 'MinimumCreepage' then R.MinimumCreepage := MMsToCoord(2.5)
    else if N = 'MinDistance' then R.MinDistance := MMsToCoord(2.5)
    else if N = 'MinimumDistance' then R.MinimumDistance := MMsToCoord(2.5)
    else if N = 'Minimum' then R.Minimum := MMsToCoord(2.5)
    else if N = 'CreepageGap' then R.CreepageGap := MMsToCoord(2.5)
    else if N = 'Value' then R.Value := MMsToCoord(2.5);
    L('OK ' + N + ' -> ' + R.GetState_DataSummaryString);
  except
    L('no ' + N);
  end;
end;

procedure Body;
var Doc, Board, R: Variant;
begin
  Doc := CreateNewDocumentFromDocumentKind('PCB');
  Board := PCBServer.GetCurrentPCBBoard;
  R := PCBServer.PCBRuleFactory(eRule_Creepage);
  L('summary default: ' + R.GetState_DataSummaryString);
  TryP(R, 'MinCreepage'); TryP(R, 'Creepage'); TryP(R, 'MinimumCreepage'); TryP(R, 'MinDistance');
  TryP(R, 'MinimumDistance'); TryP(R, 'Minimum'); TryP(R, 'CreepageGap'); TryP(R, 'Value');
  Doc.SetModified(False);
  Client.CloseDocument(Doc);
end;
